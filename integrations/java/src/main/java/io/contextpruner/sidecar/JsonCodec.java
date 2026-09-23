package io.contextpruner.sidecar;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Minimal strict JSON codec used to keep the Sidecar client dependency-free. */
public final class JsonCodec {
    private JsonCodec() {}

    public static String stringify(Object value) {
        StringBuilder output = new StringBuilder();
        write(value, output);
        return output.toString();
    }

    public static Object parse(String source) {
        Parser parser = new Parser(source == null ? "" : source);
        Object value = parser.value();
        parser.whitespace();
        if (!parser.finished()) {
            throw parser.error("Unexpected trailing content");
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String source) {
        Object value = parse(source);
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("Expected a JSON object");
        }
        return (Map<String, Object>) value;
    }

    private static void write(Object value, StringBuilder output) {
        if (value == null) {
            output.append("null");
        } else if (value instanceof String || value instanceof Character) {
            string(String.valueOf(value), output);
        } else if (value instanceof Boolean) {
            output.append(value);
        } else if (value instanceof Number) {
            Number number = (Number) value;
            double decimal = number.doubleValue();
            if (Double.isNaN(decimal) || Double.isInfinite(decimal)) {
                throw new IllegalArgumentException("JSON does not support non-finite numbers");
            }
            output.append(value);
        } else if (value instanceof Map) {
            output.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!(entry.getKey() instanceof String)) {
                    throw new IllegalArgumentException("JSON object keys must be strings");
                }
                if (!first) output.append(',');
                first = false;
                string((String) entry.getKey(), output);
                output.append(':');
                write(entry.getValue(), output);
            }
            output.append('}');
        } else if (value instanceof Iterable) {
            output.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) output.append(',');
                first = false;
                write(item, output);
            }
            output.append(']');
        } else if (value.getClass().isArray()) {
            output.append('[');
            int length = java.lang.reflect.Array.getLength(value);
            for (int index = 0; index < length; index++) {
                if (index > 0) output.append(',');
                write(java.lang.reflect.Array.get(value, index), output);
            }
            output.append(']');
        } else {
            throw new IllegalArgumentException("Unsupported JSON value: " + value.getClass());
        }
    }

    private static void string(String value, StringBuilder output) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"': output.append("\\\""); break;
                case '\\': output.append("\\\\"); break;
                case '\b': output.append("\\b"); break;
                case '\f': output.append("\\f"); break;
                case '\n': output.append("\\n"); break;
                case '\r': output.append("\\r"); break;
                case '\t': output.append("\\t"); break;
                default:
                    if (character < 0x20) {
                        output.append(String.format("\\u%04x", (int) character));
                    } else {
                        output.append(character);
                    }
            }
        }
        output.append('"');
    }

    private static final class Parser {
        private final String source;
        private int index;

        private Parser(String source) {
            this.source = source;
        }

        private Object value() {
            whitespace();
            if (finished()) throw error("Expected a JSON value");
            char current = source.charAt(index);
            if (current == '{') return object();
            if (current == '[') return array();
            if (current == '"') return string();
            if (current == 't') return literal("true", Boolean.TRUE);
            if (current == 'f') return literal("false", Boolean.FALSE);
            if (current == 'n') return literal("null", null);
            if (current == '-' || Character.isDigit(current)) return number();
            throw error("Unexpected character '" + current + "'");
        }

        private Map<String, Object> object() {
            expect('{');
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (consume('}')) return result;
            while (true) {
                whitespace();
                if (finished() || source.charAt(index) != '"') {
                    throw error("Expected an object key");
                }
                String key = string();
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
                if (consume('}')) return result;
                expect(',');
            }
        }

        private List<Object> array() {
            expect('[');
            ArrayList<Object> result = new ArrayList<>();
            whitespace();
            if (consume(']')) return result;
            while (true) {
                result.add(value());
                whitespace();
                if (consume(']')) return result;
                expect(',');
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (!finished()) {
                char current = source.charAt(index++);
                if (current == '"') return result.toString();
                if (current != '\\') {
                    if (current < 0x20) throw error("Control character in string");
                    result.append(current);
                    continue;
                }
                if (finished()) throw error("Incomplete escape sequence");
                char escaped = source.charAt(index++);
                switch (escaped) {
                    case '"': result.append('"'); break;
                    case '\\': result.append('\\'); break;
                    case '/': result.append('/'); break;
                    case 'b': result.append('\b'); break;
                    case 'f': result.append('\f'); break;
                    case 'n': result.append('\n'); break;
                    case 'r': result.append('\r'); break;
                    case 't': result.append('\t'); break;
                    case 'u': result.append(unicode()); break;
                    default: throw error("Invalid escape sequence");
                }
            }
            throw error("Unterminated string");
        }

        private char unicode() {
            if (index + 4 > source.length()) throw error("Incomplete unicode escape");
            String digits = source.substring(index, index + 4);
            index += 4;
            try {
                return (char) Integer.parseInt(digits, 16);
            } catch (NumberFormatException error) {
                throw error("Invalid unicode escape");
            }
        }

        private Number number() {
            int start = index;
            consume('-');
            if (consume('0')) {
                // A leading zero is complete unless a fraction or exponent follows.
            } else {
                digits();
            }
            boolean decimal = false;
            if (consume('.')) {
                decimal = true;
                digits();
            }
            if (!finished() && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                decimal = true;
                index++;
                if (!finished() && (source.charAt(index) == '+' || source.charAt(index) == '-')) index++;
                digits();
            }
            String encoded = source.substring(start, index);
            try {
                return decimal ? Double.valueOf(encoded) : Long.valueOf(encoded);
            } catch (NumberFormatException error) {
                throw error("Invalid number");
            }
        }

        private void digits() {
            int start = index;
            while (!finished() && Character.isDigit(source.charAt(index))) index++;
            if (start == index) throw error("Expected a digit");
        }

        private Object literal(String encoded, Object value) {
            if (!source.startsWith(encoded, index)) throw error("Invalid literal");
            index += encoded.length();
            return value;
        }

        private boolean consume(char expected) {
            if (!finished() && source.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!consume(expected)) throw error("Expected '" + expected + "'");
        }

        private void whitespace() {
            while (!finished()) {
                char current = source.charAt(index);
                if (current != ' ' && current != '\n' && current != '\r' && current != '\t') return;
                index++;
            }
        }

        private boolean finished() {
            return index >= source.length();
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at character " + index);
        }
    }
}
