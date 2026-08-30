//! Writing a query string: the one place a value is escaped on its way into a URL.
//!
//! Stands for `urllib.parse.urlencode` and `quote_plus`, which the Python viewer reaches for in
//! four modules. A page's links and the citation beside them go through here, so what a footer
//! says was bound and what its link binds cannot spell a value two ways.

/// One value as `quote_plus` writes it: unreserved characters through, a space as `+`.
pub fn quoted(value: &str) -> String {
    escaped(value, "", true)
}

/// The escape both spellings share. `safe` is what goes through beside the unreserved set, and
/// `plus` is the one thing `quote_plus` does that `quote` does not.
fn escaped(value: &str, safe: &str, plus: bool) -> String {
    let mut written = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                written.push(byte as char);
            }
            b' ' if plus => written.push('+'),
            _ if safe.as_bytes().contains(&byte) => written.push(byte as char),
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

/// One path segment as `quote` writes it, which leaves `/` alone.
///
/// An offload file's name is a path, and the route it goes into is a path too — so the separators
/// stay separators where [`quoted`] would escape them.
pub fn quoted_path(value: &str) -> String {
    escaped(value, "/", false)
}
