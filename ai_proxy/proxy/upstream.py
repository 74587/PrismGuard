"""
上游 HTTP 客户端封装
"""
import httpx
import json
from typing import Optional, Dict, Any
from fastapi.responses import StreamingResponse, JSONResponse


class UpstreamClient:
    """上游服务客户端"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=60.0)
    
    async def forward_request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: Optional[Dict[str, Any]] = None,
        is_stream: bool = False
    ):
        """
        转发请求到上游
        """
        # 过滤掉不需要的头，并移除 Accept-Encoding 以避免 zstd 压缩
        filtered_headers = {
            k: v for k, v in headers.items()
            if k.lower() not in ["host", "content-length", "accept-encoding"]
        }
        # 明确请求 gzip 或不压缩（httpx 支持 gzip 自动解压）
        filtered_headers["Accept-Encoding"] = "gzip, deflate, identity"
        
        url = f"{self.base_url}{path}"
        
        try:
            if is_stream:
                # 流式请求
                async def stream_generator():
                    async with self.client.stream(
                        method,
                        url,
                        headers=filtered_headers,
                        json=body
                    ) as response:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream"
                )
            else:
                # 非流式请求（httpx 会自动处理 gzip 解压）
                response = await self.client.request(
                    method,
                    url,
                    headers=filtered_headers,
                    json=body
                )
                
                # 尝试解析 JSON
                try:
                    content = response.json()
                except Exception:
                    # 非 JSON 响应，返回文本
                    content = {"text": response.text, "status_code": response.status_code}
                
                return JSONResponse(
                    status_code=response.status_code,
                    content=content
                )
        
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "UPSTREAM_ERROR",
                        "message": f"Upstream request failed: {str(e)}",
                        "type": "upstream_error"
                    }
                }
            )
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()