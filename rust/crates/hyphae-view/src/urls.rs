//! Writing a query string: the one place a value is escaped on its way into a URL.
//!
//! Stands for `urllib.parse.urlencode` and `quote_plus`, which the Python viewer reaches for in
//! four modules. A page's links and the citation beside them go through here, so what a footer
//! says was bound and what its link binds cannot spell a value two ways.

/// One value as `quote_plus` writes it: unreserved characters through, a space as `+`.
pub fn quoted(value: &str) -> String {
    let mut written = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                written.push(byte as char);
            }
            b' ' => written.push('+'),
            _ => written.push_str(&format!("%{byte:02X}")),
        }
    }
    written
}

/// A query string body — `a=1&b=2` — with both halves of each pair quoted.
pub fn query(pairs: &[(&str, String)]) -> String {
    pairs
        .iter()
        .map(|(key, value)| format!("{}={}", quoted(key), quoted(value)))
        .collect::<Vec<_>>()
        .join("&")
}
