use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

fn murmurhash3_x86_32(data: &[u8], seed: u32) -> i32 {
    let c1: u32 = 0xcc9e2d51;
    let c2: u32 = 0x1b873593;
    let length = data.len() as u32;
    let mut h1 = seed;
    let rounded_end = data.len() & !3;

    let mut i = 0usize;
    while i < rounded_end {
        let mut k1 = u32::from(data[i])
            | (u32::from(data[i + 1]) << 8)
            | (u32::from(data[i + 2]) << 16)
            | (u32::from(data[i + 3]) << 24);
        k1 = k1.wrapping_mul(c1);
        k1 = k1.rotate_left(15);
        k1 = k1.wrapping_mul(c2);

        h1 ^= k1;
        h1 = h1.rotate_left(13);
        h1 = h1.wrapping_mul(5).wrapping_add(0xe6546b64);
        i += 4;
    }

    let mut k1 = 0u32;
    match data.len() & 3 {
        3 => {
            k1 ^= u32::from(data[rounded_end + 2]) << 16;
            k1 ^= u32::from(data[rounded_end + 1]) << 8;
            k1 ^= u32::from(data[rounded_end]);
        }
        2 => {
            k1 ^= u32::from(data[rounded_end + 1]) << 8;
            k1 ^= u32::from(data[rounded_end]);
        }
        1 => {
            k1 ^= u32::from(data[rounded_end]);
        }
        _ => {}
    }
    if (data.len() & 3) != 0 {
        k1 = k1.wrapping_mul(c1);
        k1 = k1.rotate_left(15);
        k1 = k1.wrapping_mul(c2);
        h1 ^= k1;
    }

    h1 ^= length;
    h1 ^= h1 >> 16;
    h1 = h1.wrapping_mul(0x85ebca6b);
    h1 ^= h1 >> 13;
    h1 = h1.wrapping_mul(0xc2b2ae35);
    h1 ^= h1 >> 16;

    h1 as i32
}

fn preprocess(text: &str, lowercase: bool) -> String {
    if lowercase {
        text.to_lowercase()
    } else {
        text.to_owned()
    }
}

fn sanitize_text(text: &str, lowercase: bool) -> String {
    let clean = text.replace('\r', " ").replace('\n', " ");
    preprocess(&clean, lowercase)
}

fn collapse_multi_whitespace(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut whitespace_run_len = 0usize;
    let mut single_whitespace = '\0';

    for ch in text.chars() {
        if ch.is_whitespace() {
            whitespace_run_len += 1;
            if whitespace_run_len == 1 {
                single_whitespace = ch;
            }
            continue;
        }

        if whitespace_run_len >= 2 {
            out.push(' ');
        } else if whitespace_run_len == 1 {
            out.push(single_whitespace);
        }
        whitespace_run_len = 0;
        out.push(ch);
    }

    if whitespace_run_len >= 2 {
        out.push(' ');
    } else if whitespace_run_len == 1 {
        out.push(single_whitespace);
    }

    out
}

fn collect_char_boundaries(text: &str) -> Vec<usize> {
    let mut boundaries: Vec<usize> = text.char_indices().map(|(idx, _)| idx).collect();
    boundaries.push(text.len());
    boundaries
}

fn add_char_ngrams(
    counts: &mut HashMap<usize, f64>,
    text: &str,
    min_n: usize,
    max_n: usize,
    n_features: usize,
    alternate_sign: bool,
) {
    let collapsed = collapse_multi_whitespace(text);
    let boundaries = collect_char_boundaries(&collapsed);
    let text_len = boundaries.len().saturating_sub(1);
    let mut current_min = min_n;

    if current_min == 1 {
        for i in 0..text_len {
            let gram = &collapsed[boundaries[i]..boundaries[i + 1]];
            add_hashed_gram(counts, gram, n_features, alternate_sign);
        }
        current_min += 1;
    }

    let upper = usize::min(max_n + 1, text_len + 1);
    for n in current_min..upper {
        for i in 0..=(text_len - n) {
            let gram = &collapsed[boundaries[i]..boundaries[i + n]];
            add_hashed_gram(counts, gram, n_features, alternate_sign);
        }
    }
}

fn add_word_ngrams(
    counts: &mut HashMap<usize, f64>,
    text: &str,
    min_n: usize,
    max_n: usize,
    n_features: usize,
    alternate_sign: bool,
) {
    let tokens: Vec<&str> = text.split_whitespace().collect();
    let mut current_min = min_n;

    if max_n == 1 {
        for tok in tokens {
            add_hashed_gram(counts, tok, n_features, alternate_sign);
        }
        return;
    }

    if current_min == 1 {
        for tok in &tokens {
            add_hashed_gram(counts, tok, n_features, alternate_sign);
        }
        current_min += 1;
    }

    let upper = usize::min(max_n + 1, tokens.len() + 1);
    for n in current_min..upper {
        for i in 0..=(tokens.len() - n) {
            let gram = tokens[i..i + n].join(" ");
            add_hashed_gram(counts, &gram, n_features, alternate_sign);
        }
    }
}

fn add_hashed_gram(
    counts: &mut HashMap<usize, f64>,
    gram: &str,
    n_features: usize,
    alternate_sign: bool,
) {
    let h = murmurhash3_x86_32(gram.as_bytes(), 0);
    let idx = (h.unsigned_abs() as usize) % n_features;
    let sign = if alternate_sign && h < 0 { -1.0 } else { 1.0 };
    *counts.entry(idx).or_insert(0.0) += sign;
}

fn normalize_l2(counts: &mut HashMap<usize, f64>) {
    let l2 = counts.values().map(|v| v * v).sum::<f64>().sqrt();
    if l2 > 0.0 {
        for value in counts.values_mut() {
            *value /= l2;
        }
    }
}

#[pyfunction]
#[pyo3(signature = (text, analyzer, ngram_range, n_features, alternate_sign=false, norm=None, lowercase=true))]
fn extract_features(
    text: &str,
    analyzer: &str,
    ngram_range: (usize, usize),
    n_features: usize,
    alternate_sign: bool,
    norm: Option<&str>,
    lowercase: bool,
) -> PyResult<HashMap<usize, f64>> {
    if n_features == 0 {
        return Err(PyValueError::new_err("n_features must be > 0"));
    }

    let (min_n, max_n) = ngram_range;
    if min_n == 0 || max_n == 0 || min_n > max_n {
        return Err(PyValueError::new_err("invalid ngram_range"));
    }

    let text = sanitize_text(text, lowercase);
    let mut counts = HashMap::new();

    match analyzer {
        "char" => add_char_ngrams(
            &mut counts,
            &text,
            min_n,
            max_n,
            n_features,
            alternate_sign,
        ),
        "word" => add_word_ngrams(
            &mut counts,
            &text,
            min_n,
            max_n,
            n_features,
            alternate_sign,
        ),
        _ => {
            return Err(PyValueError::new_err(format!(
                "Unsupported analyzer: {analyzer}"
            )))
        }
    }

    if norm == Some("l2") && !counts.is_empty() {
        normalize_l2(&mut counts);
    }

    Ok(counts)
}

#[pymodule]
fn hashlinear_rust_ext(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_features, m)?)?;
    Ok(())
}
