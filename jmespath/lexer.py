import string
import warnings
from json import loads

from jmespath.exceptions import LexerError, EmptyExpressionError


class Lexer(object):
    """Tokeniser for JMESPath expressions.

    The historical implementation walked a ``list(expression)`` copy with a
    ``_next()`` method call per character, testing ``set`` membership through
    ``self.`` attribute lookups.  This version keeps exactly the same branch
    order and the same token offsets, but:

    * scans the string in place with an integer cursor (no per-character list
      copy, no ``_next()`` call, no ``self._current`` store),
    * hoists the character-class lookups into locals,
    * builds identifier/number text with a single slice instead of repeated
      string concatenation.

    ``start``/``end`` in the emitted tokens match upstream byte for byte,
    including its quirk for delimited tokens (see ``_delimited_end``).
    """

    START_IDENTIFIER = set(string.ascii_letters + '_')
    VALID_IDENTIFIER = set(string.ascii_letters + string.digits + '_')
    VALID_NUMBER = set(string.digits)
    WHITESPACE = set(" \t\n\r")
    SIMPLE_TOKENS = {
        '.': 'dot',
        '*': 'star',
        ']': 'rbracket',
        ',': 'comma',
        ':': 'colon',
        '@': 'current',
        '(': 'lparen',
        ')': 'rparen',
        '{': 'lbrace',
        '}': 'rbrace',
    }

    def tokenize(self, expression):
        # Nothing (not even ``EmptyExpressionError``) happens until the first
        # ``next()``, matching the historical generator behaviour.
        if not expression:
            raise EmptyExpressionError()
        src = expression
        length = len(src)
        self._expression = src
        self._length = length
        self._position = 0

        simple_tokens = self.SIMPLE_TOKENS
        start_identifier = self.START_IDENTIFIER
        valid_identifier = self.VALID_IDENTIFIER
        valid_number = self.VALID_NUMBER
        whitespace = self.WHITESPACE

        index = 0
        while index < length:
            char = src[index]

            token_type = simple_tokens.get(char)
            if token_type is not None:
                yield {'type': token_type, 'value': char,
                       'start': index, 'end': index + 1}
                index += 1
                continue

            if char in start_identifier:
                start = index
                index += 1
                while index < length and src[index] in valid_identifier:
                    index += 1
                yield {'type': 'unquoted_identifier', 'value': src[start:index],
                       'start': start, 'end': index}
                continue

            if char in whitespace:
                index += 1
                continue

            if char == '[':
                start = index
                if index + 1 < length:
                    following = src[index + 1]
                    if following == ']':
                        index += 2
                        yield {'type': 'flatten', 'value': '[]',
                               'start': start, 'end': start + 2}
                        continue
                    if following == '?':
                        index += 2
                        yield {'type': 'filter', 'value': '[?',
                               'start': start, 'end': start + 2}
                        continue
                index += 1
                yield {'type': 'lbracket', 'value': '[',
                       'start': start, 'end': start + 1}
                continue

            if char == "'":
                token, index = self._delimited(src, index, "'", 'raw')
                yield token
                continue

            if char == '|':
                if index + 1 < length and src[index + 1] == '|':
                    yield {'type': 'or', 'value': '||',
                           'start': index, 'end': index + 1}
                    index += 2
                else:
                    yield {'type': 'pipe', 'value': '|',
                           'start': index, 'end': index}
                    index += 1
                continue

            if char == '&':
                if index + 1 < length and src[index + 1] == '&':
                    yield {'type': 'and', 'value': '&&',
                           'start': index, 'end': index + 1}
                    index += 2
                else:
                    yield {'type': 'expref', 'value': '&',
                           'start': index, 'end': index}
                    index += 1
                continue

            if char == '`':
                token, index = self._delimited(src, index, '`', 'literal')
                yield token
                continue

            if char in valid_number:
                start = index
                index += 1
                while index < length and src[index] in valid_number:
                    index += 1
                yield {'type': 'number', 'value': int(src[start:index]),
                       'start': start, 'end': index}
                continue

            if char == '-':
                start = index
                index += 1
                # ``-`` starts a number only when digits follow immediately.
                while index < length and src[index] in valid_number:
                    index += 1
                if index - start == 1:
                    raise LexerError(lexer_position=start, lexer_value='-',
                                     message="Unknown token '-'")
                yield {'type': 'number', 'value': int(src[start:index]),
                       'start': start, 'end': index}
                continue

            if char == '"':
                token, index = self._delimited(src, index, '"',
                                               'quoted_identifier')
                yield token
                continue

            if char == '<':
                if index + 1 < length and src[index + 1] == '=':
                    yield {'type': 'lte', 'value': '<=',
                           'start': index, 'end': index + 1}
                    index += 2
                else:
                    yield {'type': 'lt', 'value': '<',
                           'start': index, 'end': index}
                    index += 1
                continue

            if char == '>':
                if index + 1 < length and src[index + 1] == '=':
                    yield {'type': 'gte', 'value': '>=',
                           'start': index, 'end': index + 1}
                    index += 2
                else:
                    yield {'type': 'gt', 'value': '>',
                           'start': index, 'end': index}
                    index += 1
                continue

            if char == '!':
                if index + 1 < length and src[index + 1] == '=':
                    yield {'type': 'ne', 'value': '!=',
                           'start': index, 'end': index + 1}
                    index += 2
                else:
                    yield {'type': 'not', 'value': '!',
                           'start': index, 'end': index}
                    index += 1
                continue

            if char == '=':
                if index + 1 < length and src[index + 1] == '=':
                    yield {'type': 'eq', 'value': '==',
                           'start': index, 'end': index + 1}
                    index += 2
                else:
                    raise LexerError(lexer_position=index, lexer_value='=',
                                     message="Unknown token '='")
                continue

            raise LexerError(lexer_position=index, lexer_value=char,
                             message="Unknown token %s" % char)

        self._position = length
        yield {'type': 'eof', 'value': '', 'start': length, 'end': length}

    # -- delimited runs (raw strings, literals, quoted identifiers) ---------

    def _delimited(self, src, start, delimiter, kind):
        """Scan a delimited run and return ``(token, next_index)``.

        Backslash escapes the following character (both characters are kept
        verbatim), and an unterminated run raises ``LexerError`` positioned at
        the opening delimiter -- exactly like the original ``_consume_until``.
        """
        length = len(src)
        index = start + 1
        while index < length:
            char = src[index]
            if char == '\\':
                index += 2
                continue
            if char == delimiter:
                break
            index += 1
        if index >= length:
            raise LexerError(lexer_position=start,
                             lexer_value=src[start:],
                             message="Unclosed %s delimiter" % delimiter)

        raw = src[start + 1:index]
        index += 1  # skip the closing delimiter
        end = self._delimited_end(start, index, length)

        if kind == 'raw':
            return ({'type': 'literal', 'value': raw.replace("\\'", "'"),
                     'start': start, 'end': end}, index)

        if kind == 'quoted_identifier':
            text = '"' + raw + '"'
            try:
                return ({'type': 'quoted_identifier', 'value': loads(text),
                         'start': start, 'end': end}, index)
            except ValueError as e:
                error_message = str(e).split(':')[0]
                raise LexerError(lexer_position=start,
                                 lexer_value=text,
                                 message=error_message)

        # Backtick literal: try JSON first, then the JEP-12 legacy string.
        lexeme = raw.replace('\\`', '`')
        try:
            parsed = loads(lexeme)
        except ValueError:
            try:
                parsed = loads('"%s"' % lexeme.lstrip())
                warnings.warn("deprecated string literal syntax",
                              PendingDeprecationWarning)
            except ValueError:
                raise LexerError(lexer_position=start,
                                 lexer_value=src[start:],
                                 message="Bad token %s" % lexeme)
        return ({'type': 'literal', 'value': parsed,
                 'start': start, 'end': end}, index)

    def _delimited_end(self, start, after, length):
        """Reproduce upstream's ``end`` value for delimited tokens.

        Upstream stored ``cursor_position - token_start`` here: the cursor
        stopped *on* the closing delimiter, so the value is the token length
        only when the delimiter is not the final character.  Reproduced
        verbatim to keep token streams byte-for-byte compatible.
        """
        size = after - start
        if after >= length:
            size -= 1
        return size
