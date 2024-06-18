#! /usr/bin/env python3

'''Extract clock offsets and rates from IVS VLBI station log files

examples:

    >> read_log('i00000wz.log')
    ({('fmout2', 'gps'): [
        (datetime(2018, 1, 3, 18, 30, 42), 2.9978e-06),
        (datetime(2018, 1, 3, 18, 47, 42), 3.0183e-06),
        (datetime(2018, 1, 3, 19, 8, 50),  2.9913e-06),
        (datetime(2018, 1, 3, 19, 30, 25), 2.9923e-06)
    ]}, [...])

    >> fit_fmout(['./path/to/skd', '/path/to/logs'])
    Fits(session='R41061', fits={
        'Ht': Fit(
            peculiar_offset=2.929e-06,
            measured_offset=5.890515923127345e-06,
            adjusted_offset=8.819515923127345e-06,
            rate=9.705096082395652e-14,
            n=330,
            mode='fmout-gps',
            path='./r41061ht.log'
        ), ...
    }, epoch=datetime.datetime(2022, 7, 28, 18, 30))
'''

from datetime import datetime, timedelta
from typing import Callable, Iterable, Literal, Mapping, Tuple, Union
from typing import NamedTuple
import argparse
import os
import re
import stat
import sys
from vlbi import ROOT

# Phillip's favorite log file clock line:
# na254ny.log: 9807014320724/gps-fmout/++;;;;;;;/;;o/?/++9.999981265E-001

# Maximum time to allow a year-less log entry's time stamp to jump backwards
# before assuming it's year actually changed.
MAX_LOG_JUMPBACK = timedelta(14)

# Header line containing log file's year, for use with 9-digit time stamps:
# 364154936;MARK IV Field System Version  8.2 matera20 1997 72435701
RE_HEADER = re.compile(r'''
    .*mark\ [^\ ]*\ field\ system\ version\ *[\d.]+\ [^\ ]+\ *(\d{4})\b
''', re.X).match

# Time stamp with format yyyy.ddd.HH:MM:SS.00, yydddHHMMSS00, or dddHHMMSS
RE_TIMESTAMP = r'(?:\d{4}\.\d{3}\.\d{2}:\d{2}:\d{2}\.\d{2}|\d{9}(?:\d{4})?)\b'
RE_TIMESTAMP = re.compile(RE_TIMESTAMP).match

RE_TYPE = r'''
    (?:
        gps|(?:h-?)?maser|dbe|dbbcout|(?:fm|form(?:atter)?)(?:oun?t)
        |mk[345]b?|stm|st1pps|tac|pps
    )[0-9]*
'''
# This regex parses the whole clock offset log line except for time stamp
# groups: source, target, suffix, value, prefix, unit, clock_time
RE_OFFSET = re.compile(rf'''
    # source, separator, target, suffix
    \b({RE_TYPE})(?:-|_|\ |\bto\b)*({RE_TYPE})(.*?)

    # offset value
    # >= 2 digit integers to filter spurious 1-digit integer between key/value
    # \b before 1st digit prevents interpreting stray tokens as numbers
    ((?:[+-]\ *)?(?:[.,]\d+|\b\d+[.,]\d*|\b\d{{2,}})(?:\ *e\ *[+-]?\d+)?)
    (?!.?[0-9+-])  # Avoid common malformatted log file 1.2.3... pattern

    # units
    (?:\ *\b(micro|mic|u|nano|n|milli|mili|m)?(sec(?:ond)?s?|s)?\b)

    # timestamp
    (?:.*?(?:@|\bat)\ *(\d\d\d[/.-]\d\d:\d\d)\ *utc?\b)?
''', re.X).findall

RE_PEC_OFFSET = re.compile(r'^([^#]\S+)\s+\S+\s+(\S+)\s+\S+', re.M)
RE_SECTION = re.compile(r'^\$([A-Z0-9a-z_-]*)((?:(?!\$).*$\n?)*)', re.M)
RE_SKD_START = re.compile(r'(?:^|\s)START\s+(\d{13})(?:\s|$)')
RE_SKD_SUBNET = re.compile(r'^Subnet\s+(.*)', re.M | re.I)
RE_SKD_STATION = re.compile(r'^P\s+(\S\S)\s', re.M)
RE_VEX_COMMENT = re.compile(r'\*.*')
RE_VEX_CLOCK = re.compile(r'(?:^|(?<=;))\s*clock_early\s*=([^;]*)', re.S)
RE_VEX_DEF = r'(?:^|(?<=;))\s*def\s*([^;]*);((?:(?!\s*enddef\b)[^;]*;)*)'
RE_VEX_DEF = re.compile(RE_VEX_DEF, re.S)
RE_VEX_EPOCH = r'\s*(\d{4})y(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\s*$'
RE_VEX_EPOCH = re.compile(RE_VEX_EPOCH)
RE_VEX_EXPER = re.compile(r';\s*exper_name\s*=\s*([^;]*)', re.S)
RE_VEX_START = r';\s*(?:exper_nominal_)?start\s*=\s*([\dydhms]+)'
RE_VEX_START = re.compile(RE_VEX_START, re.S)

UNITS = {
    'milli': 1e-3, 'mili': 1e-3, 'm': 1e-3,
    'micro': 1e-6, 'mic': 1e-6, 'u': 1e-6, 'nano': 1e-9, 'n': 1e-9
}

is_sched = lambda p: p.lower().endswith(('.vex', '.ovex', '.skd'))

def is_log(path: str) -> bool:
    '''test if path could be a station log'''
    p = os.path.basename(path).lower()
    return len(p) >= 6 and p.endswith('.log') and p.count('.') == 1

def info(text: str, verbose: bool = True):
    '''Show verbose info'''
    if verbose:
        _i, i_ = ('\033[2m', '\033[22m') if sys.stderr.isatty() else ('', '')
        sys.stderr.write(_i + text.rstrip('\n') + i_ + '\n')

def warn(text: str, verbose: bool = True):
    '''Show warning info'''
    if verbose:
        _i, i_ = ('\033[33m', '\033[39m') if sys.stderr.isatty() else ('', '')
        sys.stderr.write(_i + 'WARNING: ' + text.rstrip('\n') + i_ + '\n')

class ClockType(NamedTuple):
    '''Source and target clock type for a log's clock entry'''
    source: str  # e.g. "fmout" from fmout-gps
    target: str  # e.g. "gps" from fmout-gps

class Clock(NamedTuple):
    '''Epoch and offset in seconds from a single clock reading from the log'''
    epoch: datetime
    offset: float  # seconds

class Log(NamedTuple):
    '''Parsed log info'''
    clocks: Mapping[ClockType, Clock]
    comments: Iterable[str]

def read_log(log: Union[str, Iterable[str]]) -> Log:
    '''Convert IVS station log to clock offsets, comments respectively

    * `log` is a file path, lines list, or open text file
    * comments are apparently clock-related lines which weren't parsed
    '''
    if isinstance(log, str):
        with open(log, encoding='utf-8', errors='replace') as file:
            return read_log(file)
    ref_year = None
    last_log_time = datetime(1, 1, 1)
    clocks = {}
    comments = []
    for line in log:
        l = line.lower()
        # detect year-setting header line for files without years in timestamps
        # 040192410;MARK IV FIELD SYSTEM VERSION  8.2 MATERA20 1994 72435701
        if 'field system version' in l:
            if r:= RE_HEADER(l):
                ref_year = int(r.group(1))
            continue
        # pre-filter to gps/maser lines
        if not ('gps' in l or 'maser' in l or 'tac' in l or 'pps' in l):
            # scan more deeply for comments
            if ('dbe' in l or 'dbbc' in l or 'form' in l or (
                'mk' in l and ('mk3' in l or 'mk4' in l or 'mk5' in l)
            )):
                comments.append(line)
            continue
        # filter comments
        if 'comment' in l:
            comments.append(line)
            continue
        # match offset pattern
        for src, tgt, suf, val, pre, unit, epoch in RE_OFFSET(l):
            # remove comment/checklist items
            if ('less' in suf or 'greater' in suf or 'than' in suf or (
                not (unit or pre) and ',' in val and not 'e' in val.lower()
            )):
                comments.append(line)
                continue
            # parse log entry time stamp
            if not (t := (RE_TIMESTAMP(l) or [None])[0]):
                comments.append(line)
                continue
            try:
                # yyyy.ddd.HH:MM:SS.00
                if len(t) == 20:
                    v = t[0:4], t[5:8], t[9:11], t[12:14], t[15:17], t[18:20]
                    y, d, h, m, s, p = map(int, v)
                    t = datetime(y, 1, 1, h, m, s, p * 10000) + timedelta(d - 1)
                # yydddHHMMSS00
                elif len(t) == 13:
                    v = t[0:2], t[2:5], t[5:7], t[7:9], t[9:11], t[11:13]
                    y, d, h, m, s, p = map(int, v)
                    y += 1900 if y >= 70 else 2000
                    t = datetime(y, 1, 1, h, m, s, p * 10000) + timedelta(d - 1)
                # dddHHMMSS
                else:
                    if not ref_year:
                        comments.append(line)
                        continue
                    y, d, h, m, s = ref_year, t[0:3], t[3:5], t[5:7], t[7:9]
                    d, h, m, s = int(d), int(h), int(m), int(s)
                    t = datetime(y, 1, 1, h, m, s) + timedelta(d - 1)
                    # add a year if it looks like we jumped back due to
                    # end-of-year rollover (e.g. Dec 31 -> Jan 1)
                    if (last_log_time - t) > MAX_LOG_JUMPBACK:
                        ref_year += 1
                        t = datetime(ref_year, 1, 1, h, m, s) + timedelta(d - 1)
                last_log_time = t
                # parse clock offset time stamp (if present)
                # ddd/HH:MM
                if epoch:
                    y, d, h, m = t.year, epoch[0:3], epoch[4:6], epoch[7:9]
                    d, h, m = int(d), int(h), int(m)
                    # use sec=30 (center of min) since precision is only 1 min
                    epoch = datetime(y, 1, 1, h, m, 30) + timedelta(d - 1)
                    # add a year if it looks like we jumped back due to
                    # end-of-year rollover (e.g. Dec 31 -> Jan 1)
                    if (t - epoch) > MAX_LOG_JUMPBACK:
                        t = datetime(y + 1, 1, 1, h, m, 30) + timedelta(d - 1)
                    else:
                        t = epoch
            except (ValueError, OverflowError):
                comments.append(line)
                continue
            # get unit from prefix, or assume sec (exponent) or usec (no exp)
            unit = UNITS.get(pre, 1.0 if 'e' in val else 1e-6)
            # convert value to float
            val = unit * float(''.join(val.split()).replace(',', '.'))
            # sort source and target by rank
            source_rank, target_rank = ((
                7 if 'f' in txt else  # fmout|...
                6 if 'd' in txt else  # dbe|dbbcout
                5 if 'k' in txt else  # mk[345]
                4 if 'st' in txt else  # stm|st1pps
                3 if 'm' in txt else  # maser
                2 if 'g' in txt else  # gps
                1 if 't' in txt else  # tac
                0  # pps
            ) for txt in (src, tgt))
            if (source_rank, src) < (target_rank, tgt):
                src, tgt = tgt, src
                val *= -1.0
            # store value
            clocks.setdefault(ClockType(src, tgt), []).append(Clock(t, val))
    return Log(clocks, comments)

def find_files(
    paths: Iterable[str], valid: Callable[[str], bool], _visited=None
) -> Iterable[str]:
    '''Find file(s) in `paths` matching `valid(path)`'''
    found, _visited = [], set() if _visited is None else _visited
    for path in [paths] if isinstance(paths, str) else paths:
        st = os.stat(path)
        if (st.st_dev, st.st_ino) in _visited:
            continue
        _visited.add((st.st_dev, st.st_ino))
        if stat.S_ISREG(st.st_mode):
            if valid(path):
                found.append(path)
        elif stat.S_ISDIR(st.st_mode):
            subpaths = [os.path.join(path, file) for file in os.listdir(path)]
            found.extend(find_files(subpaths, valid, _visited))
    return found

def vex_epoch(text: str) -> datetime:
    '''Convert date from `####y###d##h##m##s` format to `datetime`'''
    if r := RE_VEX_EPOCH.match(text):
        y, d, h, m, s = [int(i or 0) for i in r.groups()]
        try:
            return datetime(y, 1, 1, h, m, s) + timedelta(d - 1)
        except (ValueError, OverflowError):
            pass
    return datetime.max

VEX_UNITS = {
    'psec': 1e-12, 'nsec': 1e-9, 'usec': 1e-6, 'msec': 1e-3,
    '': 1, 'sec': 1, 'min': 60, 'hr': 3600, 'yr': 31557600
}
def vex_sec(text: str) -> float:
    '''Convert value from `1.5 usec` format to number of seconds'''
    try:
        num, units = (text.split() + ['', ''])[:2]
        return float(num) * VEX_UNITS[units.lower()]
    except KeyError as e:
        raise ValueError from e

class ClockEarly(NamedTuple):
    '''VEX `clock_early` line'''
    valid: datetime
    offset: float
    epoch: datetime
    rate: float
    measured: float

class Sched(NamedTuple):
    '''Schedule file content'''
    session: str
    ambiguity: int
    start: datetime
    clocks: Mapping[str, Iterable[ClockEarly]]

def read_sched(path: str) -> Sched:
    '''Read a VEX or SKED schedule file'''
    session, ambiguity, start, stations, clocks = None, 999, None, set(), {}
    with open(path) as f:
        f = f.read()
    # SKD (SKED)
    if path.rpartition('.')[2].lower() == 'skd':
        f = {r[1].upper(): r[2] for r in RE_SECTION.finditer(f)}
        # $EXPER session
        session = f.get('EXPER', '').strip() or None
        # $PARAM START yyyydddHHMMSS
        if ambiguity and (r := RE_SKD_START.search(f.get('PARAM', ''))):
            ambiguity, start = 0, datetime.strptime(r[1], '%Y%j%H%M%S')
        # $SKED _  _ _ _ yydddHHMMSS
        elif ambiguity > 1:
            starts = []
            for line in f.get('SKED', '').splitlines():
                if len(line := line.split()) >= 5:
                    starts.append(datetime.strptime(line[4], '%y%j%H%M%S'))
            ambiguity, start = 1, min(starts) if starts else None
        # $MAJOR Subnet KkWz
        for r in RE_SKD_SUBNET.findall(f.get('MAJOR', '')):
            r = ''.join(r.split())
            n = len(r) // 2 * 2
            stations.update(r[i:(i + 2)].capitalize() for i in range(0, n, 2))
        # $STATIONS P Kk ...
        r = RE_SKD_STATION.findall(f.get('STATIONS', ''))
        stations.update(i.capitalize() for i in r)
    # VEX
    else:
        f = RE_VEX_COMMENT.sub('', f)
        f = {r[1].upper(): r[2] for r in RE_SECTION.finditer(f)}
        # $EXPER; def _; exper_name = session : segment;
        if r := RE_VEX_EXPER.search(f.get('EXPER', '')):
            session = ''.join(i.strip() for i in r[1].split(':')) or None
        # $EXPER; def session;
        if not session and (r := RE_VEX_DEF.search(f.get('EXPER', ''))):
            session = r[1].strip() or None
        # $EXPER; def _; exper_nominal_start = start;
        if ambiguity and (r := RE_VEX_START.search(f.get('EXPER', ''))):
            ambiguity, start = 0, vex_epoch(r[1])
        # $SCHED; scan _; start = start;
        elif ambiguity > 2:
            starts = RE_VEX_START.findall(f.get('SCHED', ''))
            starts = list(map(vex_epoch, starts))
            ambiguity, start = 2, min(starts) if starts else None
        # $CLOCK; def station; clock
        for r in RE_VEX_DEF.finditer(f.get('CLOCK', '')):
            stations.add(station := r[1].strip())
            for c in RE_VEX_CLOCK.findall(r[2]):
                c = [i.strip() for i in c.split(':')]
                n, valid, offset = len(c), vex_epoch(c[0]), vex_sec(c[1])
                clocks.setdefault(station, []).append(ClockEarly(
                    valid, offset, vex_epoch(c[2]) if n > 2 and c[2] else valid,
                    float(n > 3 and c[3] or 0), vex_sec(c[6]) if n > 6 else None
                ))
    clocks.update((id, []) for id in stations if id not in clocks)
    return Sched(session.upper(), ambiguity, start, clocks)

def read_scheds(paths: Iterable[str], verbose: bool = False) -> Sched:
    '''Get session name, start time, and stations from schedule files'''
    s = Sched(None, 999, None, {})
    for path in find_files(paths, is_sched):
        info(f'reading {path}', verbose)
        s2 = read_sched(path)
        amb, start = min([(s.ambiguity, s.start), (s2.ambiguity, s2.start)])
        s = Sched(s.session or s2.session, amb, start, {
            i: s.clocks.get(i) or s2.clocks.get(i) or []
            for i in (set(s.clocks) | set(s2.clocks))
        })
    return s

def total_absolute_deviations(
    slope_intercept: Tuple[float, float], x: Iterable[float], y: Iterable[float]
) -> float:
    '''Total absolute deviations (for `scipy.optimize.minimize`)'''
    a, b = slope_intercept
    return sum(abs(y - (a * x + b)))

class Fit(NamedTuple):
    '''Result from `fit_fmout`

    * all offsets and intercepts are in sec
    * `mode` is the clock measurement mode (e.g. 'fmout-gps', 'maser-gps')
    '''
    peculiar_offset: float = 0.0
    measured_offset: float = None
    adjusted_offset: float = None
    rate: float = None
    n: int = 0
    mode: str = None
    path: str = None

class Fits(NamedTuple):
    '''Session name, map of station ID to fit, and epoch from `fit_fmout`'''
    session: str
    fits: Mapping[str, Fit]
    epoch: datetime

def fit_fmout(
    paths: Iterable[str], *,
    session: str = None,
    clock_offsets: str = os.path.join(ROOT, 'etc/clock.offset'),
    epoch: datetime = None,
    rounds: int = 3,
    avg: Literal['mean', 'median'] = 'median',
    sigma: float = 3,
    delta: float = 1e-06,
    plot: str = None,
    verbose: bool = False
) -> Fits:
    '''Fit fmout data and return station name to fit `dict`, and start time

    * `paths` paths to station logs and VEX/SKD schedule file(s)
    * `session` override session name
    * `clock_offsets` path to peculiar offsets file
    * `epoch` epoch for reported offsets (default: session start)
    * `rounds` outlier rejection # of rounds
    * `avg` outlier rejection averaging method
    * `sigma` outlier rejection threshold (# of standard deviations)
    * `delta` difference between two points for a clock break
    * `plot` path in which to save plots
    '''
    # expensive imports
    import numpy as np
    import scipy.optimize
    import scipy.stats
    import matplotlib.pyplot as plt
    # read schedule(s)
    sched = read_scheds(paths, verbose)
    session = session.upper() if session else sched.session
    epoch = epoch or sched.start
    # find logs
    logs = find_files(paths, is_log)
    logs = {path[-6:-4].capitalize(): path for path in logs}
    # read peculiar offsets
    pecoffs = {}
    if clock_offsets:
        info(f'reading {clock_offsets}', verbose)
        with open(clock_offsets) as file:
            pecoffstr = file.read()
            #pecoffs = {
            #    cols[0].capitalize(): 1e-6 * float(cols[1])
            #    for cols in (line.split() for line in file) if len(cols) == 4 and not line.startswith('#')
            #}
        for val in RE_PEC_OFFSET.finditer(pecoffstr):
            pecoffs[val.group(1)] = 1e-6 * float(val.group(2))
    # Read log file for each station, get fmout and time data, mark if log is
    # missing or unreadable
    fits = {}
    for station in sorted(set(sched.clocks) | set(logs)):
        if not (log := logs.get(station)):
            fits[station] = Fit(pecoffs.get(station, 0.0))
            continue
        info(f'reading {log}', verbose)
        clocks = read_log(log)[0]
        # Cycle through possible timing options,
        # from most desirable (fmout-gps) to least
        src_prefs = 'f', 'd', 'k', 'st', 'm', 'g', 't', ''
        tgt_prefs = 'f', 'd', 'k', 'st', 'm', 'p', 'g', 't', ''
        prefs = {(
            next(i for i, c in enumerate(src_prefs) if c in src),
            next(j for j, c in enumerate(tgt_prefs) if c in tgt)
        ): (src, tgt) for src, tgt in clocks}
        # Skip if not a field system log
        if not prefs:
            warn(f'no clock information found for {station} in {log}')
            continue
        src, tgt = prefs[min(prefs)]
        out = clocks[(src, tgt)]
        mode = f'{src}-{tgt}'
        if 'f' not in src:
            warn(f'station {station} using {mode}', verbose)
        if not out:
            fits[station] = Fit(pecoffs.get(station, 0), path=log)
            continue
        # Convert measurements and dates to arrays
        measures = np.array([x[1] for x in out])
        dates = np.array([(x[0] - epoch).total_seconds() for x in out])
        # Save copy of measurements for plotting
        orig = measures.copy() if plot else None
        # Reject outliers with 'nrejects' rounds of 'nsigma' sigma rejection
        avg_func = np.nanmean if avg.lower() == 'mean' else np.nanmedian
        n = 0
        for i in range(rounds):
            m, std = avg_func(measures), np.nanstd(measures)
            rejects, = np.where(np.abs(measures - m) > sigma * std)
            if not rejects.size:
                break
            n += rejects.size
            measures[rejects] = np.nan
        info(f'rejected {n} outliers from {station}\n', verbose and n)
        # Remove entries after clock breaks (`ytol` chooses how far to break)
        for i, (v0, v1, o) in enumerate(zip(measures, measures[1:], out)):
            if abs(v0 - v1) >= delta:
                if verbose:
                    msg = f'clock break detected for station {station}'
                    sys.stdout.write(f'{msg} at {o.epoch:%Y-%j %H:%M:%S}\n')
                measures[(i + 1):] = np.nan
                break
        # Fit line to measurements
        measured_intercept = adjusted_intercept = rate = None
        # Mask NaNs for fitting functions
        mask = ~np.isnan(measures)
        n = np.sum(mask)
        if n > 1:
            # Make initial guess w/ linear regression
            fit = scipy.stats.linregress(dates[mask], measures[mask])
            # Fit data by minimizing least absolute deviation (LAD) function
            res = scipy.optimize.minimize(
                total_absolute_deviations, (fit.slope, fit.intercept),
                args=(dates[mask], measures[mask]), method='Nelder-Mead'
            )
            rate, measured_intercept = res.x
            adjusted_intercept = pecoffs.get(station, 0) + measured_intercept
            if rate == 0.0:
                warn(f'station {station} has a rate of 0.0', verbose)
        elif n == 1:
            if verbose:
                t = epoch + timedelta(0, dates[mask][0])
                msg = f'station {station} has only one valid point: offset ='
                warn(f'{msg} {measures[mask][0]} sec, t = {t:%Y-%j %H:%M:%S}')
            measured_intercept = measures[mask][0]
            adjusted_intercept = pecoffs.get(station, 0) + measured_intercept
        # Update 'statfits'
        fits[station] = Fit(
            pecoffs.get(station), measured_intercept, adjusted_intercept,
            rate, n, mode, log
        )
        # Make two plots per station: all data, and only good data
        if plot:
            sess, st = session.lower(), station.lower()
            for good in [False, True]:
                # Plot all data
                if good:
                    plt.plot(dates[mask], measures[mask], 'ko')
                else:
                    plt.plot(dates[mask], orig[mask], 'ko')
                    plt.plot(dates[~mask], orig[~mask], 'ro')
                x = np.array([dates[0], dates[-1]])
                plt.plot(x, rate * x + measured_intercept, 'b-')
                msg = ' (no outliers)' if good else ''
                plt.title(f'{session} {station} {mode} vs time{msg}')
                plt.xlabel('Time from epoch (sec)')
                plt.ylabel(f'{mode} (sec)')
                label = 'good' if good else 'all'
                path = os.path.join(plot, f'{sess}{st}_{mode}_{label}.png')
                if verbose:
                    sys.stdout.write(f'writing {path}\n')
                plt.savefig(path)
                plt.clf()
    return Fits(session, fits, epoch)

def DATE(text: str) -> datetime:
    '''Convert text from yyyymmddHHMM or YYYYdddhhmm to datetime'''
    r = r'(\d{4})\D?(?:(\d{3})|(\d\d)\D?(\d\d))\D?(\d\d)?\D?(\d\d)?$'
    if r := re.match(r, text):
        if r[2]:
            t = datetime(int(r[1]), 1, 1, int(r[5] or 0), int(r[6] or 0))
            return t + timedelta(int(r[2]) - 1)
        v = int(r[1]), int(r[3]), int(r[4]), int(r[5] or 0), int(r[6] or 0)
        return datetime(*v)
    raise ValueError

def main():
    '''Run script'''
    # Parse args
    A = argparse.ArgumentParser(description=__doc__.partition('\n')[0])
    A.add_argument('path', default='.', nargs='*', help=(
        'paths to station logs and VEX or SKED file, '
        'or session name to find paths automatically (default: %(default)s)'
    ))
    A.add_argument(
        '-c', '--clock-offsets', metavar='PATH',
        default=os.path.join(ROOT, 'etc/clock.offset'),
        help='peculiar offset file'
    )
    A.add_argument('-e', '--epoch', type=DATE, metavar='EPOCH', help=(
        'epoch for reported offsets as an ISO format datetime'
        ' (default: session start)'
    ))
    A.add_argument(
        '-p', '--plot', action='store_const', const='.',
        help='save plots to current working directory'
    )
    A.add_argument(
        '-P', '--plot-to', metavar='DIR', dest='plot', help='save plots to DIR'
    )
    A.add_argument(
        '-m', '--median', action='store_const', dest='avg', const='median',
        help='use median for outlier rejection (the default)', default='median'
    )
    A.add_argument(
        '-M', '--mean', action='store_const', dest='avg', const='mean',
        help='use mean for outlier rejection'
    )
    A.add_argument(
        '-r', '--rounds', metavar='N', type=int, default=3,
        help='outlier rejection # of rounds'
    )
    A.add_argument(
        '-s', '--sigma', metavar='σ', type=float, default=3,
        help='outlier rejection threshold (# of standard deviations)'
    )
    A.add_argument(
        '-d', '--delta', metavar='SEC', type=float, default=1e-6,
        help='difference between two points for a clock break'
    )
    A.add_argument('-v', '--verbose', action='store_true', help='show details')
    a = A.parse_args()

    # auto-find paths
    if len(a.path) == 1 and os.path.sep not in a.path:
        if not os.path.isdir(a.path[0]):
            p = a.path[0].lower()
            t = re.match(r'([a-zA-Z]*)(.*)', p)[1]
            a.path = [os.path.join(ROOT, 'run', *i) for i in [[p], [t, p]]]
            a.path = [path for path in a.path if os.path.exists(path)] or a.path
            info(f'using path {",".join(a.path)}', a.verbose)

    # Fit data
    session, fits, epoch = fit_fmout(
        a.path, clock_offsets=a.clock_offsets, epoch=a.epoch, rounds=a.rounds,
        avg=a.avg, sigma=a.sigma, delta=a.delta, plot=a.plot, verbose=a.verbose
    )
    # Print results to screen
    for station, v in sorted(fits.items()):
        missing = ['peculiar offset'] if v.peculiar_offset is None else []
        missing += ['log clocks'] if v.measured_offset is None else []
        if missing:
            warn(f'station {station} has no {", ".join(missing)}')
    sys.stdout.write(f'* Session {session} at {epoch:%Y-%j %H:%M:%S}\n')
    sys.stdout.write(f'*{"validity_epoch":>39}    offset_interval')
    sys.stdout.write('         offset_epoch             rate')
    if a.verbose:
        sys.stdout.write('\033[2m           *  measured_offset')
        sys.stdout.write('  peculiar_offset  points  source\033[22m')
    sys.stdout.write('\n')
    e = f'{epoch:%Yy%jd%Hh%Mm%Ss}'
    for station, (po, mo, ao, r, n, mode, _) in sorted(fits.items()):
        o = 1e6 * ((po or mo or 0.0) if ao is None else ao)
        msg = f'clock_early = {e} :{o:12.3f} usec : {e} : {r or 0.0:14.6e};'
        sys.stdout.write(f'def {station}; {msg} enddef;')
        if a.verbose:
            po = f'{"N/A":^17}' if po is None else f'{po * 1e6:12.3f} usec'
            mo = f'{"N/A":^17}' if mo is None else f'{mo * 1e6:+12.3f} usec'
            msg = f'  *{mo}{po} {n:7d}  {mode}'.rstrip()
            msg = f'\033[2m{msg}\033[22m' if sys.stdout.isatty() else msg
            sys.stdout.write(msg)
        sys.stdout.write('\n')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.stderr.write('\n')
        sys.stderr.close()
