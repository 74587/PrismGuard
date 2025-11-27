"""
主路由处理模块 - 支持多来源格式和工具调用
"""
import json
import urllib.parse
from typing import Optional, Tuple
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from ai_proxy.moderation.basic import basic_moderation
from ai_proxy.moderation.smart.ai import smart_moderation
from ai_proxy.transform.extractor import extract_text_from_internal
from ai_proxy.transform.formats.parser import detect_and_parse, get_parser
from ai_proxy.transform.formats.internal_models import InternalChatRequest
from ai_proxy.proxy.upstream import UpstreamClient

router = APIRouter()


def parse_url_config(cfg_and_upstream: str) -> Tuple[dict, str]:
    """
    解析 URL 中的配置和上游地址
    格式: {encoded_json_config}${upstream_url}
    """
    parts = cfg_and_upstream.split("$", 1)
    if len(parts) != 2:
        raise HTTPException(400, "Invalid URL format: expected {config}${upstream}")
    
    try:
        cfg_str = urllib.parse.unquote(parts[0])
        config = json.loads(cfg_str)
        upstream = parts[1]
        return config, upstream
    except Exception as e:
        raise HTTPException(400, f"Config parse error: {str(e)}")


async def process_request(
    config: dict,
    body: dict,
    path: str,
    headers: dict
) -> Tuple[bool, Optional[str], Optional[dict], Optional[str]]:
    """
    处理请求审核和格式转换
    
    Returns:
        (通过, 错误信息, 转换后的body, 源格式名称)
    """
    # 格式转换配置
    transform_cfg = config.get("format_transform", {})
    transform_enabled = transform_cfg.get("enabled", False)
    
    if not transform_enabled:
        # 不转换，直接审核原始 body
        from ai_proxy.transform.extractor import extract_text_for_moderation
        text = extract_text_for_moderation(body, "openai_chat")
        
        # 基础审核
        if config.get("basic_moderation", {}).get("enabled"):
            passed, reason = basic_moderation(text, config["basic_moderation"])
            if not passed:
                return False, reason, None, None
        
        # 智能审核
        if config.get("smart_moderation", {}).get("enabled"):
            passed, result = await smart_moderation(text, config["smart_moderation"])
            if not passed:
                return False, f"Smart moderation: {result.reason}", None, None
        
        return True, None, body, None
    
    # 检测并解析来源格式
    config_from = transform_cfg.get("from", "auto")
    src_format, internal_req = detect_and_parse(config_from, path, headers, body)
    
    if src_format is None:
        # 无法识别格式，透传
        print(f"[INFO] Cannot detect format, pass through")
        return True, None, body, None
    
    print(f"[INFO] Detected format: {src_format}")
    
    # 从内部格式抽取文本进行审核
    text = extract_text_from_internal(internal_req)
    
    # 基础审核
    if config.get("basic_moderation", {}).get("enabled"):
        passed, reason = basic_moderation(text, config["basic_moderation"])
        if not passed:
            return False, reason, None, src_format
    
    # 智能审核
    if config.get("smart_moderation", {}).get("enabled"):
        passed, result = await smart_moderation(text, config["smart_moderation"])
        if not passed:
            return False, f"Smart moderation: {result.reason}", None, src_format
    
    # 格式转换
    target_format = transform_cfg.get("to", src_format)
    
    if target_format == src_format:
        # 目标格式与源格式相同，不转换
        transformed_body = body
    else:
        # 转换到目标格式
        target_parser = get_parser(target_format)
        if target_parser is None:
            print(f"[WARN] Target format {target_format} not supported, use source format")
            transformed_body = body
        else:
            try:
                transformed_body = target_parser.to_format(internal_req)
                print(f"[INFO] Transformed from {src_format} to {target_format}")
            except Exception as e:
                print(f"[ERROR] Transform failed: {e}")
                return False, f"Format transform error: {str(e)}", None, src_format
    
    return True, None, transformed_body, src_format


@router.api_route("/{cfg_and_upstream:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_entry(cfg_and_upstream: str, request: Request):
    """
    代理入口 - 支持多来源格式检测和转换
    """
    # 解析配置
    try:
        config, upstream_base = parse_url_config(cfg_and_upstream)
    except HTTPException as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "error": {
                    "code": "CONFIG_PARSE_ERROR",
                    "message": e.detail,
                    "type": "config_error"
                }
            }
        )
    
    # 获取请求体
    try:
        body = await request.json() if request.method in ["POST", "PUT"] else {}
    except:
        body = {}
    
    # 获取请求路径
    path = request.url.path
    
    # 处理审核和转换
    passed, error_msg, transformed_body, src_format = await process_request(
        config, body, path, dict(request.headers)
    )
    
    if not passed:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "MODERATION_BLOCKED",
                    "message": error_msg,
                    "type": "moderation_error",
                    "source_format": src_format
                }
            }
        )
    
    # 转发到上游
    upstream_client = UpstreamClient(upstream_base)
    
    # 构建完整路径（移除配置部分）
    proxy_path = path.replace(f"/{cfg_and_upstream}", "", 1)
    if not proxy_path:
        proxy_path = "/"
    
    # 转发请求
    try:
        response = await upstream_client.forward_request(
            method=request.method,
            path=proxy_path,
            headers=dict(request.headers),
            body=transformed_body if transformed_body else body,
            is_stream=body.get("stream", False) if isinstance(body, dict) else False
        )
        return response
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "PROXY_ERROR",
                    "message": f"Proxy request failed: {str(e)}",
                    "type": "proxy_error"
                }
            }
        )