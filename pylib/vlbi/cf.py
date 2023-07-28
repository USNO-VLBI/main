#! /usr/bin/env python3

'''Read and reformat HOPS fourfit configuration (CF) file

usage:

* `c = CF('r12345.cf')` read CF file
* `c = CF().read(['sb_win -256.0 256.0'])` read CF file from `str` line(s)
* `sys.stdout.write(str(c))` write out with standardized CF file format
* `c.eval(station='K').match` get fully matched properties for Mk4 ID `K`
* `c.eval(station='K').maybe` get incomplete matches for same
* `eval()` accepts multiple keywords
* `eval(...).maybe` properties need additional keywords to disambiguate
'''

from typing import Any, Iterable, List, Mapping, Set, Union
import argparse
import collections.abc
import datetime
import io
import operator
import re
import sys

CHAR_ARRAY_TOKENS = {'freqs'}
STR_ARRAY_TOKENS = {'samplers'}
RE_CHAN = re.compile(r'^[a-zA-Z_%][+-]?$')
RE_COMMENT = re.compile(r'\*[^\n]*')
RE_TOKEN = re.compile(r'<>|[<>()]|[^<>()\s]+')
SAME_AS_START = object()
UTC_000_000000 = datetime.timedelta(-1)
UTC_999_999999 = datetime.timedelta(1002, 16839)
_PROPERTY = lambda i, doc: property(operator.itemgetter(i), doc=doc)

class Alias(int):
    '''CF file integer with text alias'''

    def __new__(cls, value: int, text: str):
        x = super().__new__(cls, value)
        x.text: str = text
        return x

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({int(self)}, {self.text!r})'

    def __str__(self) -> str:
        return self.text

ALIASES = {
    'true': Alias(1, 'true'),
    'false': Alias(0, 'false'),
    'keep': Alias(32767, 'keep'),
    'discard': Alias(0, 'discard')
}

def utc_epoch(delta: datetime.timedelta) -> str:
    '''Convert timedelta to CF file UTC epoch'''
    if isinstance(delta, str):
        return delta
    if delta <= UTC_000_000000:
        return '000-000000'
    s = delta.seconds + 86400 * (1 + delta.days)
    d = min(999, s // 86400)
    s -= 86400 * d
    h = min(99, s // 3600)
    s -= 3600 * h
    m = min(99, s // 60)
    return f'{d:03d}-{h:02d}{m:02d}{min(99, s - 60 * m):02d}'

def utc_timedelta(text: str) -> datetime.timedelta:
    '''Convert CF file UTC epoch text to timedelta from Jan 1st'''
    if len(text) != 10 or text[3] != '-':
        raise ValueError(text)
    doy, h, m, s = map(int, (text[:3], text[4:6], text[6:8], text[8:10]))
    return datetime.timedelta(doy - 1, h * 3600 + m * 60 + s)

class Condition:
    '''CF file conditional statement'''

    def eval(self, **kwargs) -> bool:
        '''Evaluation based on keywork arguments'''
        raise NotImplementedError

    def __and__(self, o: Union['Condition', 'Conditions']):
        '''Create conditions with AND'''
        return AND(self, o)

    def __or__(self, o: Union['Condition', 'Conditions']):
        return OR(self, o)

class Conditions(Condition, collections.abc.MutableSequence):
    '''CF file conditional statement (abstract)'''

    def __init__(self, *conditions: Union[Condition, 'Conditions']):
        self._terms = []
        for i in conditions:
            if isinstance(i, self.__class__):
                self._terms.extend(i)
            else:
                self._terms.append(i)

    def eval(self, **kwargs) -> bool:
        '''Evaluation based on keywork arguments'''
        raise NotImplementedError

    def __and__(self, o: Union[Condition, 'Conditions']):
        '''Create conditions with AND'''
        return AND(self, o)

    def __or__(self, o: Union[Condition, 'Conditions']):
        return OR(self, o)

    def insert(self, i: int, condition: Condition):
        self._terms.insert(i, condition)

    def __getitem__(self, i: int) -> Condition:
        return self._terms[i]

    def __setitem__(self, i: int, condition: Condition):
        self._terms[i] = condition

    def __delitem__(self, i: int):
        del self._terms[i]

    def __len__(self) -> int:
        return len(self._terms)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({", ".join(map(repr, self._terms))})'

class Bool(Condition):
    '''CF file generic true or false conditional statement'''

    def __init__(self, value: bool = False):
        self._value = bool(value)

    def eval(self, **kwargs) -> bool:
        '''Always true for TRUE'''
        return self._value

    def __repr__(self) -> str:
        return 'TRUE' if self._value else 'FALSE'

TRUE, FALSE = Bool(True), Bool(False)

class AND(Conditions):
    '''CF file `and` conditional statement'''

    def eval(self, **kwargs) -> bool:
        '''Evaluation based on keywork arguments'''
        result = True if self else None
        for i in (c.eval(**kwargs) for c in self):
            if i is None:
                result = None
            elif not i:
                return False
        return result

    def __str__(self) -> str:
        return (' and '.join(
            (f'({c})' if isinstance(c, OR) else str(c)) for c in self
        ))

class OR(Conditions):
    '''CF file `or` conditional statement'''

    def eval(self, **kwargs) -> bool:
        '''Evaluation based on keywork arguments'''
        result = False if self else None
        for i in (c.eval(**kwargs) for c in self):
            if i is None:
                result = None
            elif i:
                return True
        return result

    def __str__(self) -> str:
        return ' or '.join(map(str, self))

class NOT(Conditions):
    '''CF file `not` conditional statement'''

    def __init__(self, term: Condition):
        super().__init__(term)

    def eval(self, **kwargs) -> bool:
        '''Evaluation based on keywork arguments'''
        return not self[0].eval(**kwargs)

    def insert(self, i, x):
        if i != 0 or self._terms:
            raise IndexError(i)
        self._terms.insert(i, x)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self._terms[0]!r})'

    def __setitem__(self, i: int, x: Condition):
        if i != 0:
            raise IndexError(i)
        self._terms[i] = x

    def __str__(self) -> str:
        nots, c = 'not ', self[0]
        while isinstance(c, NOT):
            nots += 'not '
            c = c[0]
        return f'{nots}({c})' if isinstance(c, Conditions) else f'{nots}{c}'

class Prop(Condition):
    '''CF file condition that a property has a certain value'''

    def __init__(self, name: str, value: Any):
        if not (isinstance(name, str) and isinstance(value, str)):
            raise ValueError('str required for name and value')
        self.name, self.value = name, value

    def eval(self, **kwargs) -> bool:
        '''Evaluate to whether kwargs match property, None if N/A'''
        if self.name not in kwargs:
            return None
        r = re.escape(self.value).replace(r'\?', '.*').replace(r'\*', '.*')
        return bool(re.match(r, kwargs[self.name]))

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.name!r}, {self.value!r})'

    def __str__(self) -> str:
        return self.name + ' ' + self.value

class Scan(Condition):
    '''CF file condition that a scan time be in a range'''

    name = 'scan'

    def __init__(
        self,
        start: Union[str, datetime.timedelta, None],
        stop: Union[str, datetime.timedelta, None] = SAME_AS_START
    ):
        '''Initialize with two epoch offsets, understands `<` and `>`'''
        if isinstance(start, datetime.timedelta):
            start = utc_epoch(start)
        elif start is not None and not isinstance(start, str):
            raise TypeError('(str|timedelta|None) required for start')
        if stop is SAME_AS_START:
            stop = start
        elif isinstance(stop, datetime.timedelta):
            stop = utc_epoch(stop)
        elif stop is not None and not isinstance(stop, str):
            raise TypeError('(str|timedelta|None) required for stop')
        self.start, self.stop = start, stop

    def eval(self, **kwargs) -> bool:
        '''Evaluate to whether kwargs match property, None if N/A'''
        if self.name not in kwargs:
            return None
        t = kwargs[self.name]
        t = utc_timedelta(t) if isinstance(t, str) else t
        if self.start is None:
            return t < self.stop
        if self.stop is None:
            return t > self.start
        return self.start <= t <= self.stop

    def __repr__(self) -> str:
        if self.start == self.stop:
            return f'{self.__class__.__name__}({self.start!r})'
        return f'{self.__class__.__name__}({self.start!r}, {self.stop!r})'

    def __str__(self) -> str:
        if self.start is None:
            return '< ' + utc_epoch(self.stop)
        if self.stop is None:
            return '> ' + utc_epoch(self.start)
        if self.start == self.stop:
            return utc_epoch(self.start)
        return utc_epoch(self.start) + ' to ' + utc_epoch(self.stop)

def _parse_condition(tokens: List[str], lazy: bool = False) -> Condition:
    '''Conditional from reversed tokens'''
    the_list = []
    while True:
        next = tokens.pop()
        if next == 'scan':
            o = tokens.pop()
            if o and o in '<>':
                if o == '<':
                    p = Scan(None, tokens.pop())
                elif o == '>':
                    p = Scan(tokens.pop(), None)
                else:
                    p = Scan(tokens.pop())
                the_list.append(NOT(p) if o == '<>' else p)
            else:
                if tokens and tokens[-1] == 'to':
                    tokens.pop()
                    the_list.append(Scan(o, tokens.pop()))
                else:
                    the_list.append(Scan(o, o))
        elif next == 'not':
            the_list.append(NOT(_parse_condition(tokens, True)))
        elif next == '(':
            the_list.append(_parse_condition(tokens))
            next = tokens.pop()
            if next != ')':
                raise ValueError(next)
        else:
            the_list.append(Prop(next, tokens.pop()))
        if lazy or not tokens or tokens[-1] not in ('and', 'or'):
            break
        the_list.append(tokens.pop())
    # join ands
    i = 0
    while i  + 2 < len(the_list):
        if the_list[i + 1] == 'and':
            ands = the_list[i] = AND(the_list[i], the_list[i + 2])
            del the_list[(i + 1):(i + 3)]
            while i + 2 < len(the_list) and the_list[i + 1] == 'and':
                ands.append(the_list[i + 2])
                del the_list[(i + 1):(i + 3)]
        else:
            i += 2
    # join ors
    ors = [the_list[i] for i in range(0, len(the_list), 2)]
    return OR(*ors) if len(ors) > 1 else ors[0]

class IF(tuple):
    '''CF file if / else block'''

    condition: Condition = _PROPERTY(0, 'Conditional statement')
    true: Mapping[str, Any] = _PROPERTY(1, 'Properties if true')
    false: Mapping[str, Any] = _PROPERTY(2, 'Properties if false')

    def __new__(
        cls,
        condition: condition = None,
        true: Mapping[str, Any] = None,
        false: Mapping[str, Any] = None
    ):
        if condition is None:
            condition = TRUE
        elif not isinstance(condition, Condition):
            raise TypeError('condition must be a condition')
        if true is None:
            true = {}
        elif not isinstance(true, collections.abc.Mapping):
            raise TypeError('true must be a Mapping class')
        if false is None:
            false = {}
        elif not isinstance(false, collections.abc.Mapping):
            raise TypeError('false must be a Mapping class')
        return tuple.__new__(cls, (condition, true, false))

    def has_prop(self, name: str) -> bool:
        '''True if property name is in true or false'''
        return name in self.true or name in self.false

    @property
    def props(self) -> Set[str]:
        '''All property names, both true and false'''
        return frozenset(i for block in [self.true, self.false] for i in block)

    def __repr__(self) -> str:
        prefix = f'{self.__class__.__name__}({self.condition!r}'
        if self.true:
            if self.false:
                return f'{prefix, }, {self.true!r}, {self.false!r})'
            return f'{prefix, }, {self.true!r})'
        elif self.false:
            return f'{prefix, }, false={self.false!r})'
        return f'{prefix})'

    @staticmethod
    def _item_str(k: str, v: Any) -> str:
        # str float [float ...]
        # str [str ...]
        # float [float ...]
        # str float float [float float ...] (gates)
        # float float [float float ...] (notches)
        # convert to strings
        if isinstance(v, (int, float, str)):
            v, strs = [v], [str(v)]
        else:
            strs = list(map(str, v))
        if k.lower() in STR_ARRAY_TOKENS:
            v.insert(0, len(v))
            strs.insert(0, str(v[0]))
        paired = k.lower() in ('gates', 'notches')
        if paired:
            offset = 1 if isinstance(v[0], str) else 0
            s = k + ' ' + v[0] if offset else k
            x = (strs[i] + ' ' + strs[i + 1] for i in range(offset, len(v), 2))
            s += ('  ' if len(v) > 2 + offset else ' ') + '  '.join(x)
        else:
            s = k + ' ' + ' '.join(strs)
        return s

    def __str__(self) -> str:
        out = []
        first_block = isinstance(self.condition, Bool) and self.condition.eval()
        if not first_block:
            out.append(f'if {self.condition}')
        for k, v in self.true.items():
            if first_block:
                out.append(self._item_str(k, v))
            else:
                x = self._item_str(k, v).splitlines()
                out.append('\n'.join('  ' + i for i in x))
        if self.false:
            out.append('\nelse' if '\n' in out[0] else 'else')
        for k, v in self.false.items():
            x = self._item_str(k, v).splitlines()
            out.append('\n'.join('  ' + i for i in x))
        return '\n'.join(out)

class Eval(tuple):
    '''Results from CF.eval'''

    match: Mapping[str, Any] = _PROPERTY(0, (
        'Properties exactly matched by eval keywords'
    ))
    maybe: Mapping[str, Any] = _PROPERTY(1, (
        'Properties partially matched by eval keywords but which '
        'require additional keywords to fully match'
    ))

    def __new__(
        cls,
        match: Mapping[str, Any] = None, maybe: Mapping[str, Any] = None
    ):
        if match is None:
            match = {}
        elif not isinstance(match, collections.abc.Mapping):
            raise TypeError('match must be a Mapping class')
        if maybe is None:
            maybe = {}
        elif not isinstance(maybe, collections.abc.Mapping):
            raise TypeError('maybe must be a Mapping class')
        return tuple.__new__(cls, (match, maybe))

    def has_prop(self, name: str) -> bool:
        '''True if property name is in match or maybe'''
        return name in self.match or name in self.maybe

    @property
    def props(self) -> Set[str]:
        '''All property names, both match and maybe'''
        return frozenset(i for block in [self.match, self.maybe] for i in block)

    def __repr__(self) -> str:
        name = self.__class__.__name__
        return f'{name}(match={self.match!r}, maybe={self.maybe!r})'

class CF(collections.abc.MutableSequence):
    '''HOPS fourfit configuration (CF) file'''

    def __init__(self, src: Union[str, io.IOBase, Iterable] = None):
        '''Read CF file from file path, open text file, or content list'''
        self._data = []
        self.read(src)

    def read(self, src: Union[str, io.IOBase, Iterable] = None) -> 'CF':
        '''Read in file path, open text file, or content list, return self'''
        if isinstance(src, str):
            with open(src) as file:
                text = file.read().strip()
            self.read_text(text)
        elif src:
            try:
                self.read_text(' '.join(src).strip())
            except (ValueError, TypeError):
                self._data = list(src)
        return self

    def read_text(self, text: str) -> 'CF':
        '''Read text from CF file into this CF object, return self'''
        # parse tokens
        tokens = RE_TOKEN.findall(RE_COMMENT.sub(' ', text))
        tokens.reverse()  # so we can efficiently pop()
        condition = TRUE
        true = current = {}
        false = {}
        while tokens:
            next = tokens.pop()
            if next in STR_ARRAY_TOKENS:
                n = int(tokens.pop())
                current[next] = [tokens.pop() for _ in range(n)]
            elif next in CHAR_ARRAY_TOKENS:
                v = current.setdefault(next, [])
                while tokens and RE_CHAN.match(tokens[-1]):
                    v.append(tokens.pop())
            elif next == 'else':
                current = false
            elif next == 'if':
                if true or false or not (
                    isinstance(condition, Bool) and condition.eval()
                ):
                    self._data.append(IF(condition, true, false))
                condition = _parse_condition(tokens)
                true = current = {}
                false = {}
            else:
                values = [tokens.pop()]
                # number
                try:
                    values[0] = int(values[0])
                except ValueError:
                    try:
                        values[0] = float(values[0])
                    except ValueError:
                        values[0] = ALIASES.get(values[0], values[0])
                # (str|number),  [number, ...]
                while tokens:
                    i = tokens[-1]
                    try:
                        values.append(int(i))
                    except ValueError:
                        try:
                            values.append(float(i))
                        except ValueError:
                            break
                    tokens.pop()
                current[next] = values if len(values) > 1 else values[0]
        if true or false or not (
            isinstance(condition, Bool) and condition.eval()
        ):
            self._data.append(IF(condition, true, false))
        return self

    @classmethod
    def from_text(cls, text: str) -> 'CF':
        '''Create new CF object from CF file text'''
        if not isinstance(text, str):
            text = text.decode() if isinstance(text, bytes) else ' '.join(text)
        return cls().read_text(text)

    def eval(self, **kwargs) -> Eval:
        '''Evaluate relavent properties given conditions

        kwargs  ({str: str, ...})  criteria for eval of conditions
        return  (Eval)  tuple-like of matched and possible properties
        '''
        match, maybe = {}, {}
        for block in self:
            truth = block.condition.eval(**kwargs)
            if truth:
                match.update(block.true.items())
            elif truth is None:
                for possibility in [block.true, block.false]:
                    for key, value in possibility.items():
                        if key not in maybe:
                            maybe[key] = set()
                        try:
                            maybe[key].add(value)
                        except TypeError:
                            maybe[key].add(tuple(value))
        return Eval(match, maybe)

    def has_prop(self, name: str) -> bool:
        '''True if property name is in any block'''
        return any(block.has_prop(name) for block in self)

    @property
    def props(self) -> Set[str]:
        '''All property names, both true and false'''
        return frozenset(i for block in self for i in block.props)

    def insert(self, i: int, v: Condition):
        self._data.insert(i, v)

    def __delitem__(self, i: int):
        del self._data[i]

    def __getitem__(self, i: int) -> Condition:
        return self._data[i]

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self._data!r})'

    def __setitem__(self, i: int, v: Condition):
        self._data[i] = v

    def __str__(self) -> str:
        return '\n'.join(f'{i}\n' for i in self)

def main():
    '''Run script'''
    A = argparse.ArgumentParser(description=__doc__.partition('\n')[0])
    a = A.add_argument('path', nargs='?')
    a.help = 'CF file path (default \'\' for STDIN)'
    a = A.parse_args()
    try:
        cf = CF(a.path or sys.stdin)
    except Exception as e:
        sys.stderr.write(f'{e}\n')
        sys.exit(getattr(e, 'errno', 1))
    # write results
    sys.stdout.write(str(cf))

if __name__ == '__main__':
    main()
