#! /usr/bin/env python3

'''Set of USNO VLBI Python modules

To access VLBI modules, use syntax like:

```
>>> from vlbi import stations, skd, cf
```

Some common functions are directly in `vlbi`:

```
>>> from vlbi import ROOT  # the home of /correlator or similar

>>> vlbi.datetime('2022y288d12h30m15.123456s')
datetime.datetime(2022, 10, 15, 12, 30, 15, 123456)

>>> vlbi.mjd2datetime(59944.5)
datetime.datetime(2022, 12, 31, 12, 0)

>>> vlbi.doy('2022-12-31')
365

>>> vlbi.info('grey text to stderr')
grey text to stderr

>>> vlbi.warn('yellow text to stderr')
WARNING: yellow text to stderr

>>> vlbi.error('red text to stderr')
ERROR: red text to stderr
```
'''

from typing import Iterable as _Iterable, Iterator as _Iterator
import datetime as _datetime
import io as _io
import os as _os
import re as _re
import sys as _sys
import tempfile as _tempfile

# find VLBI correlator app root path
if (ROOT := _os.environ.get('CORRPATH')) and _os.path.isdir(ROOT):
    ROOT = _os.path.abspath(ROOT)
else:
    ROOT = _os.path.dirname(_os.path.abspath(__file__))
    while ROOT != '/' and not _os.path.exists(_os.path.join(ROOT, '.CORRROOT')):
        ROOT = _os.path.dirname(ROOT)
    if ROOT == '/' and not _os.path.exists(_os.path.join(ROOT, '.CORRROOT')):
        for ROOT in [
            _os.path.expanduser('~/opt/correlator'),
            _os.path.expanduser('~/correlator'),
            '/opt/correlator', '/correlator'
        ]:
            if _os.path.isdir(ROOT):
                break
        else:
            ROOT = _os.path.abspath('.')

# time and date tools

MJD_EPOCH = _datetime.datetime(1858, 11, 17)

_RE_DATETIME = _re.compile(r'''
    (\d{4})y(\d+)d(?:(\d*)h)?(?:(\d*)m)?(?:(\d*\.\d+|\d+)s)?$ |  # VEX
    (\d{4})\D?(?:(\d{3})|(\d\d)\D?(\d\d)) # ISO yyyy-ddd or yyyy-mm-dd
    \D?(\d\d)?\D?(\d\d)?\D?(\d\d(?:\.\d*)?)?$  # ISO hh:mm:ss.00
''', _re.X)

def rgb(
    r: float = None, g: float = None, b: float = None, bg: bool = False
) -> str:
    '''Get ANSI 256-color or 8-color code for RGB values (0-1)

    * Usage: `print(f'{rgb(1, 0, 0)}This is red!{rgb()}')`
    * Set `bg` to return background instead of foreground colors
    * Return from `rgb()` will return terminal to default colors
    * WARNING: This function assumes your terminal is capable of 256 colors!!!
    '''
    if r is None and g is None and b is None:
        return '\033[49m' if bg else '\033[39m'
    r, g, b = (max(0, min(5, int(i * 6))) for i in (r, g, b))
    return f'\033[{48 if bg else 38};5;{16 + 36 * r + 6 * g + b}m'

RE_ANSI = _re.compile(r'''
    \033(?:
        # singlge shift 2/3 (SS2/SS3)
        [NO]. |

        # device control string, operating system command, start of string,
        # privacy message, or application program command
        [P\]X\^_].*?(?:\033\\|\x07) |

        # control sequence introducer (CSI)
        \[.*?[\x40-\x7e]
    ) |
    # C0 control codes
    [\x00-\x08\x0b-\x0c\x0e-\x1f]+
''', _re.S | _re.X)

def write(
    text: str, file: _io.TextIOBase = _sys.stdout,
    *, color: bool = None, end: str = '\n', flush: bool = True
):
    '''Write line(s) to a file, strip ANSI codes and `\r`, and flush

    * `file` should be open in text mode with universal newlines
    * `color` is `True` to strip ANSI, `False` not to, `None` to auto-decide
    * `end` is always at the end of the print, `''` or falsy for N/A
    * unset `flush` to avoid flushing the file after writing
    '''
    # replace DOS and pre-Intel Mac newlines with standard (universal) newlines
    if '\r' in text:
        text = text.replace('\r\n', '\n').replace('\r', '\n')
    # detect whether file is a TTY (should allow ANSI codes)
    if color is None:
        try:
            color = file.isatty()
        except AttributeError:
            color = False
    # strip ANSI codes from non-TTY files
    if not color:
        text = RE_ANSI.sub('', text)
    # add ending (e.g. newline) and write to file
    file.write(text + end if end and not text.endswith(end) else text)
    # flush the file
    if flush:
        try:
            file.flush()
        except AttributeError:
            pass  # ignore missing file.flush()

def datetime(text: str) -> _datetime.datetime:
    '''Convert text from VEX or ISO format to `datetime`'''
    if r := _RE_DATETIME.match(text):
        # VEX time
        if r[1]:
            dd, t = int(r[2]) - 1, _datetime.datetime(int(r[1]), 1, 1)
            h, m, s = int(r[3] or 0), int(r[4] or 0), float(r[5] or 0)
        # ISO w/ day of year
        elif r[7]:
            dd, t = int(r[7]) - 1, _datetime.datetime(int(r[6]), 1, 1)
            h, m, s = int(r[10] or 0), int(r[11] or 0), float(r[12] or 0)
        # ISO w/ month and day
        else:
            dd, t = 0, _datetime.datetime(int(r[6]), int(r[8]), int(r[9]))
            h, m, s = int(r[10] or 0), int(r[11] or 0), float(r[12] or 0)
        return t + _datetime.timedelta(dd, s, hours=h, minutes=m)
    raise ValueError(f'not an ISO or VEX datetime: {text!r}')

def datetime2jd(t: _datetime.datetime) -> float:
    '''Convert `datetime` to Julian datetime'''
    return ((t - MJD_EPOCH).total_seconds() + 207360000000) / 86400.0

def jd2datetime(t: float) -> _datetime.datetime:
    '''Convert Julian Datetime (JD) to `datetime`'''
    return MJD_EPOCH + _datetime.timedelta(t - 2400000)

def datetime2mjd(t: _datetime.datetime) -> float:
    '''Convert `datetime` to Modified Julian Datetime (MJD)'''
    return (t - MJD_EPOCH).total_seconds() / 86400.0

def mjd2datetime(t: float) -> _datetime.datetime:
    '''Convert Modified Julian Datetime (MJD) to `datetime`'''
    return MJD_EPOCH + _datetime.timedelta(t)

def doy(t: _datetime.datetime) -> int:
    '''Get the integer day of year from a datetime (or datetime string)'''
    if isinstance(t, str):
        t = datetime(t)
    return (t - _datetime.datetime(t.year, 1, 1)).days + 1

# terminal output tools

_INFO_CACHE, _WARN_CACHE, _ERROR_CACHE = set(), set(), set()

def info(
    text: str, verbose: bool = True, *,
    color: bool = None, once: bool = False, nl: bool = True
):
    '''Show info to stderr with faint color

    * set `color=True`/`False` to force color on or off (default TTY only)
    * set `once=True` to remember this info and show it only the first time
    * set `lf=False` or `lf=0` to inhibit newline
    '''
    text = text.rstrip('\n')
    if once:
        if text in _INFO_CACHE:
            return
        else:
            _INFO_CACHE.add(text)
    if verbose:
        color = _sys.stderr.isatty() if color is None else color
        if color:
            if nl:
                text = '\r\033[2m' + text + '\033[22m\033[K\n'
            else:
                text = '\033[2m' + text + '\033[22m'
        elif nl:
            text += '\n'
        _sys.stderr.write(text)
        _sys.stderr.flush()

def warn(
    text: str, verbose: bool = True, *,
    color: bool = None, once: bool = False
 ):
    '''Show warning info

    * set `color=True`/`False` to force color on or off (default TTY only)
    * set `once=True` to remember this warning and show it only the first time
    '''
    text = text.rstrip('\n')
    if once:
        if text in _WARN_CACHE:
            return
        else:
            _WARN_CACHE.add(text)
    if verbose:
        color = _sys.stderr.isatty() if color is None else color
        _i, i_ = ('\r\033[33m', '\033[39m\033[K\n') if color else ('', '\n')
        _sys.stderr.write(_i + 'WARNING: ' + text + i_)
        _sys.stderr.flush()

def error(
    text: str, verbose: bool = True, *,
    color: bool = None, once: bool = False, exit: int = None
):
    '''Show error message

    * set `color=True`/`False` to force color on or off (default TTY only)
    * set `once=True` to remember this error and show it only the first time
    * set `exit` to an error code or 0 to exit the program with that code
    '''
    text = text.rstrip('\n')
    if once:
        if text in _ERROR_CACHE:
            return
        else:
            _ERROR_CACHE.add(text)
    if verbose:
        color = _sys.stderr.isatty() if color is None else color
        _i, i_ = ('\r\033[31m', '\033[39m\033[K\n') if color else ('', '\n')
        _sys.stderr.write(_i + 'ERROR: ' + text + i_)
        _sys.stderr.flush()
    if exit:
        _sys.exit(exit if isinstance(exit, int) else 1)

_RE_CHAR = _re.compile(r'''
    (?:\033(?:\[.*?[\x40-\x7E]|\\|.+?\033\\))*(?:.|$)
    (?:\033(?:\[.*?[\x40-\x7E]|\\|.+?\033\\))*
''', _re.S | _re.X)

def progress(
    text: str = '', verbose: bool = True, *,
    bar: float = 0.0, nl: bool = False, color: bool = None
):
    '''Display or clear in-line, updateable status text

    * Make sure to clear with empty `progress()` when done!
        * `info`, `warn`, and `error` clear automatically
        * `sys.stderr` and `sys.stdout` do **NOT**!
    * Call repeatedly to update progress text
    * `verbose=False` prevents progress from displaying
    * `bar` (0.0 - 1.0) to show progress bar as underline of `text`
    * `nl` to include newline (no need to clear later)
    * `color` to force ANSI codes on or off

    example:
    ```
    >>>    import time
    ...    for i in range(1, 1001):
    ...        progress(f'Processing: {i} / 1000', bar=(i / 1000))
    ...        time.sleep(0.002)
    ...    progress()  # Make sure to clear!
    ```
    '''
    if verbose:
        if _sys.stderr.isatty() if color is None else color:
            if bar:
                chars = _RE_CHAR.findall(text)
                if n := round(bar * len(chars)):
                    chars[0] = '\033[4m' + chars[0]
                    chars[n - 1] += '\033[24m'
                    text = ''.join(chars)
            nl = '\n' if text and (color or nl) else ''
            _sys.stderr.write(f'\r{text}\033[K{nl}')
        else:
            _sys.stderr.write(f'{text}\n')
        _sys.stderr.flush()

def pbar(
    items: _Iterable, verbose: bool = True, *,
    color: bool = None, fmt: str = 'processing {I} / {n} ({p:7.2%})'
) -> _Iterator:
    '''Iterate through `items` while showing a `progress()` bar

    * Make sure to clear with empty `progress()` if iteration breaks
        * `info`, `warn`, and `error` clear automatically
        * `sys.stderr` and `sys.stdout` do **NOT**!
    * `verbose=False` prevents progress from displaying
    * `color` to force ANSI codes on or off
    * `fmt` determines what text is shown on the progress bar
        * `item` = current item
        * `i` = current item number (starts at 1)
        * `I` = same as `i`, but formatted at the width of `n`
        * `n` = length of `items`
        * `w` = width of `str(n)`
        * `p` = progress fraction, 0.0 on first item through 1.0 after last item

    example:
    ```
    >>>    import time
    ...    total = 0
    ...    for i in pbar(range(100)):
    ...        total += i
    ...        time.sleep(0.005)
    ...    print(f'{total = }')
    ```
    '''
    items = list(items)
    n = len(items)
    w = len(f'{n}')
    for i, item in enumerate(items, 1):
        I = f'{i:{w}}'
        p = (i - 1) / n
        text = fmt.format(i=i, I=I, n=n, p=p, w=w)
        progress(text, verbose, bar=p, color=color)
        yield item
    progress()
    info(fmt.format(i=n, n=n, p=1), verbose)

# other

class cd:
    '''Temporarily enter a path, which is a temp dir if `path=None`'''

    def __init__(self, path: str = None):
        self._path = path
        self._prev = self._tmpdir = None

    def __enter__(self) -> str:
        self._prev = _os.getcwd()
        if self._path:
            _os.chdir(self._path)
            return self._path
        else:
            self._tmpdir = _tempfile.TemporaryDirectory('.tmp', 'vlbi.')
            return self._tmpdir.__enter__()

    def __exit__(self, *args):
        _os.chdir(self._prev)
        if not self._path:
            self._tmpdir.__exit__(*args)
