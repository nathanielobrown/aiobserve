//! Python's `json.dumps` defaults, byte for byte.
//!
//! `ToolCall.input` is the tool's arguments serialised back to JSON, and the store keeps that
//! string. Two encoders that disagree about a space after a comma, or about whether `é` is
//! written literally, produce different rows for the same transcript — so the parity oracle
//! would report a difference on every tool call in the corpus. The differences from
//! `serde_json::to_string` are all defaults CPython chose:
//!
//! - separators are `", "` and `": "`, not `","` and `":"`
//! - `ensure_ascii` is on, so every code point past `~` is written as a `\uXXXX` escape
//! - a float is written by `repr`, which switches to exponent notation outside a fixed range
//!
//! Key order is the recorded order, which is why the crate reads JSON with `preserve_order`.

use serde_json::Value;

/// One value as `json.dumps` would write it, with CPython's default separators and escapes.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write(value, &mut out);
    out
}

fn write(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => match number.as_f64().filter(|_| number.is_f64()) {
            Some(float) => out.push_str(&repr_float(float)),
            None => out.push_str(&number.to_string()),
        },
        Value::String(text) => write_string(text, out),
        Value::Array(items) => {
            out.push('[');
            for (at, item) in items.iter().enumerate() {
                if at > 0 {
                    out.push_str(", ");
                }
                write(item, out);
            }
            out.push(']');
        }
        Value::Object(members) => {
            out.push('{');
            for (at, (key, member)) in members.iter().enumerate() {
                if at > 0 {
                    out.push_str(", ");
                }
                write_string(key, out);
                out.push_str(": ");
                write(member, out);
            }
            out.push('}');
        }
    }
}

/// A string as `ensure_ascii=True` writes it: the five short escapes, `\u00XX` for the rest of
/// the control range, and `\uXXXX` for every code point past `~` — astral planes as the
/// surrogate pair CPython emits.
fn write_string(text: &str, out: &mut String) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(character),
            _ => {
                let mut units = [0u16; 2];
                for unit in character.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

/// A float as CPython's `repr` writes it: the shortest round-tripping digits, in exponent
/// notation when the decimal point sits outside `(-4, 16]`, and with a trailing `.0` when it
/// would otherwise read as an integer.
fn repr_float(float: f64) -> String {
    // Rust's `{:e}` is the same shortest round-trip digit string CPython's `dtoa` produces,
    // laid out as `d[.ddd]e[-]exp`. Only the layout differs, so re-lay it out.
    let scientific = format!("{float:e}");
    let (mantissa, exponent) = scientific
        .split_once('e')
        .expect("Rust's LowerExp always writes an exponent");
    let exponent: i32 = exponent.parse().expect("that exponent is an integer");
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    // Where the decimal point falls: the value is `0.<digits> * 10^point`.
    let point = exponent + 1;
    if point <= -4 || point > 16 {
        let head = &digits[..1];
        let tail = &digits[1..];
        let dot = if tail.is_empty() {
            String::new()
        } else {
            format!(".{tail}")
        };
        let power = point - 1;
        return format!("{sign}{head}{dot}e{}{:02}", sign_of(power), power.abs());
    }
    if point <= 0 {
        let zeros = "0".repeat((-point) as usize);
        return format!("{sign}0.{zeros}{digits}");
    }
    let point = point as usize;
    if point >= digits.len() {
        let zeros = "0".repeat(point - digits.len());
        return format!("{sign}{digits}{zeros}.0");
    }
    format!("{sign}{}.{}", &digits[..point], &digits[point..])
}

fn sign_of(power: i32) -> char {
    if power < 0 { '-' } else { '+' }
}

#[cfg(test)]
mod tests {
    use super::dumps;
    use serde_json::json;

    /// Every default that differs from `serde_json::to_string`, against the strings CPython
    /// prints for the same value. Recorded tool inputs carry all of them: a nested object of
    /// options, a path with a non-ASCII character, an offset that came back as a float.
    #[test]
    fn the_encoder_writes_what_cpython_writes() {
        // `python3 -c 'import json; print(json.dumps(...))'` for each.
        assert_eq!(dumps(&json!({})), "{}");
        assert_eq!(dumps(&json!([])), "[]");
        assert_eq!(
            dumps(&json!({"file_path": "/tmp/a", "limit": 20})),
            r#"{"file_path": "/tmp/a", "limit": 20}"#
        );
        assert_eq!(dumps(&json!(["a", "b"])), r#"["a", "b"]"#);
        assert_eq!(dumps(&json!("caf\u{e9}")), r#""caf\u00e9""#);
        // Past the BMP, as the surrogate pair CPython writes.
        assert_eq!(dumps(&json!("\u{1f331}")), r#""\ud83c\udf31""#);
        assert_eq!(dumps(&json!("a\nb\tc\"d\\e")), r#""a\nb\tc\"d\\e""#);
        // The control range and DEL: `ensure_ascii` escapes both, `serde_json` only the first.
        assert_eq!(dumps(&json!("\u{1}\u{7f}")), r#""\u0001\u007f""#);
        assert_eq!(dumps(&json!(null)), "null");
        assert_eq!(dumps(&json!(true)), "true");
        assert_eq!(dumps(&json!(-12)), "-12");
    }

    /// The float layout `repr` chooses, at both ends of the range where it switches to an
    /// exponent. A tool input rarely carries one, but `cost` and `timeout` values do.
    #[test]
    fn a_float_is_laid_out_the_way_repr_lays_it_out() {
        for (value, printed) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (1.5, "1.5"),
            (0.1, "0.1"),
            (-2.25, "-2.25"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1e-30, "1e-30"),
            (1.0 / 3.0, "0.3333333333333333"),
        ] {
            assert_eq!(dumps(&json!(value)), printed, "repr of {value}");
        }
    }
}
