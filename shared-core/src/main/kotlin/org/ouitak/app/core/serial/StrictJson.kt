package org.ouitak.app.core.serial

/** Small bounded RFC 8259 decoder, independent of Android's lenient JSONObject.
 * A separate lexical number type prevents numeric strings/booleans being coerced.
 * No exceptions containing input text escape this internal boundary. */
internal data class JsonNumber(val text: String)
internal class InvalidSerialData : IllegalArgumentException()
internal class StrictJson(private val text: String) {
    private var at = 0
    private fun bad(): Nothing = throw InvalidSerialData()
    private fun whitespace() { while (at < text.length && text[at] in " \t\r\n") at++ }
    private fun consume(c: Char): Boolean {
        whitespace()
        return if (at < text.length && text[at] == c) { at++; true } else false
    }
    fun objectValue(): Map<String, Any?> {
        val value = value(0)
        whitespace()
        if (at != text.length || value !is Map<*, *>) bad()
        @Suppress("UNCHECKED_CAST")
        return value as Map<String, Any?>
    }
    private fun value(depth: Int): Any? {
        if (depth > 8) bad()
        whitespace()
        if (at == text.length) bad()
        return when (text[at]) {
            '{' -> {
                at++
                val map = linkedMapOf<String, Any?>()
                if (!consume('}')) {
                    do {
                        whitespace()
                        if (at == text.length || text[at] != '"') bad()
                        val key = string()
                        if (map.containsKey(key) || !consume(':')) bad()
                        map[key] = value(depth + 1)
                    } while (consume(','))
                    if (!consume('}')) bad()
                }
                map
            }
            '[' -> {
                at++
                val list = mutableListOf<Any?>()
                if (!consume(']')) {
                    do { list.add(value(depth + 1)) } while (consume(','))
                    if (!consume(']')) bad()
                }
                list
            }
            '"' -> string()
            't' -> literal("true", true)
            'f' -> literal("false", false)
            'n' -> literal("null", null)
            '-', in '0'..'9' -> number()
            else -> bad()
        }
    }
    private fun literal(token: String, result: Any?): Any? {
        if (!text.startsWith(token, at)) bad()
        at += token.length
        return result
    }
    private fun number(): JsonNumber {
        val start = at
        if (text[at] == '-') at++
        if (at == text.length) bad()
        if (text[at] == '0') at++ else {
            if (text[at] !in '1'..'9') bad()
            while (at < text.length && text[at] in '0'..'9') at++
        }
        if (at < text.length && text[at] == '.') {
            at++
            val digit = at
            while (at < text.length && text[at] in '0'..'9') at++
            if (at == digit) bad()
        }
        if (at < text.length && text[at] in "eE") {
            at++
            if (at < text.length && text[at] in "+-") at++
            val digit = at
            while (at < text.length && text[at] in '0'..'9') at++
            if (at == digit) bad()
        }
        val token = text.substring(start, at)
        if (token.toDoubleOrNull()?.isFinite() != true) bad()
        return JsonNumber(token)
    }
    private fun string(): String {
        at++
        val out = StringBuilder()
        while (at < text.length) {
            val c = text[at++]
            when {
                c == '"' -> {
                    // Reject unpaired UTF-16 surrogates including escaped ones.
                    var i = 0
                    while (i < out.length) {
                        if (out[i].isHighSurrogate()) {
                            if (++i == out.length || !out[i].isLowSurrogate()) bad()
                        } else if (out[i].isLowSurrogate()) bad()
                        i++
                    }
                    return out.toString()
                }
                c == '\\' -> {
                    if (at == text.length) bad()
                    out.append(when (val escape = text[at++]) {
                        '"', '\\', '/' -> escape
                        'b' -> '\b'
                        'f' -> '\u000c'
                        'n' -> '\n'
                        'r' -> '\r'
                        't' -> '\t'
                        'u' -> {
                            if (at + 4 > text.length) bad()
                            val hex = text.substring(at, at + 4)
                            if (hex.any { it !in "0123456789abcdefABCDEF" }) bad()
                            at += 4
                            hex.toInt(16).toChar()
                        }
                        else -> bad()
                    })
                }
                c < ' ' -> bad()
                else -> out.append(c)
            }
        }
        bad()
    }
}
