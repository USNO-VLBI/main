#! /usr/bin/env python3

'''IVS correlation report V2.0

extract exising report from a VGOSDB:
  > report path/to/session/01JAN01XX.tgz

build a new report:
  > report path/too/session
  > # or
  > report path/to/fringes path/to/logs path/to/schedule.ovex
  * DOUBLE CHECK REPORT FOR ERRORS BEFORE SENDING IT!
  * Please add and adjust entries in the `+NOTES` section as needed
  * Files are searched for in the provided paths first, then default paths
  * Use `report -h` to see relevant environment variables

compatibility notes:
  * Does not properly ignore VEX literal blocks and quotes (10X slowdown to do)
  * Assumes that VEX station IDs match real station IDs (not necessarily true)

credits:
  * Written at the United States Naval Observatory (USNO) by Phillip Haftings
  * Based on earlier work by Phillip Haftings (USNO) and Andy Sargent (USNO)
  * Report format is from IVS memo on the correlation report V2.0
'''

from datetime import datetime, timedelta
from subprocess import DEVNULL, run
from typing import Callable, Iterable, Iterator, Mapping, Set, Tuple, Union
from typing import NamedTuple
import argparse
import collections
import collections.abc
import errno
import io
import itertools
import os
import pwd
import re
import shlex
import stat
import sys
import tarfile
import textwrap
import numpy
import pandas
import vlbi
import vlbi.catmap
import vlbi.cf
import vlbi.fit_fmout
import vlbi.master
import vlbi.mk4
import vlbi.stations

BLACKLIST = [
	'backup', 'back', 'bak', 'temporary', 'temp', 'tmp', 'original', 'orig',
	'scratch'
]
SOFTWARE_NAME_MAP = {'difx': 'DiFX', 'hops': 'HOPS', 'nusolve': 'nuSolve'}
LOW_DATE, HIGH_DATE = datetime(1900, 1, 1), datetime(3000, 1, 1)
NAN = float('nan')
_VEX_UNITS = {
	'psec': 1e-12, 'nsec': 1e-9, 'usec': 1e-6, 'msec': 1e-3,
	'': 1, 'sec': 1, 'min': 60, 'hr': 3600, 'yr': 31557600
}
_IS_CALC = re.compile(r'.*\.calc$', re.I).match
_IS_CF = re.compile(r'(?:^|\b|_)cf(?:$|\b|_)', re.I).search
_IS_INPUT = re.compile(r'.*\.input$', re.I).match
_R = r'(.*/)?master\d\d(?:\d\d)?(?:-[^/]*)?(?<!notes)\.txt$'
_IS_MASTER = re.compile(_R, re.I).match
_IS_VEX = re.compile(r'.*\.vex(\.obs)?$').match
_IS_OVEX = re.compile(r'.*\.ovex$').match
_R = rb'^([ \t]*%\s*CORRELATOR_REPORT_FORMAT\b|\+HEADER\s*$).*'
_IS_REPORT_CONTENT = re.compile(_R, re.I | re.M | re.S).search
_R = r'(?:.*/)?[a-zA-Z0-9$%]{2}\.[A-Z]\.[0-9]*\.(?:[A-Z0-9]{6}|[a-z{][a-z]{5})$'
_IS_ROOT_PATH = re.compile(_R).match
_R = rb'^[ \t]*Session[ \t]+(\S+)[ \t]*$'
_IS_SESSION_LINE = re.compile(_R, re.I | re.M).search
_IS_SKD = re.compile(r'.*\.skd$', re.I).match
_IS_SNR = re.compile(r'.*\.snr$', re.I).match
_R = r'(?:.*/?)(?:stations.m|m.stations|ns-codes.txt)$'
_IS_STATIONS = re.compile(_R, re.I).match
_RE_DATETIME = re.compile(r'-$|\d{4}-\d{3}-\d{4}(?:\d\d)?$')
_RE_LEGEND = re.compile(r'^\s*\*\s*(\S+)\s+(\S.*)', re.M)
_RE_PASSWD_PARENS = re.compile(r'\([^()]*\)|\[[^[\]]*\]|\{[^{}]*\}|<[^<>]*>')
_RE_READ_DASHES = re.compile(r'^ *---+ *$', re.M)
_RE_READ_SECTION = re.compile(r'^\+(\w.*)\n((?:(?!\+).*\n)*)', re.M)
_RE_READ_STRIP = re.compile(r'^ *(\*.*|---+ *|)?$\n?', re.M)
_RE_REPORT_EXTS = re.compile(r'(\.(corr|rpt|tgz|tar|gz|bz))*$', re.I)
_RE_TRAILING_WS = re.compile(r'[ \t]+$', re.M)
_RE_VALUE_SEP = re.compile(r'(?: *[,\n] *)+')
_RE_VEX_EPOCH = r'\s*(\d{4})y(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\s*$'
_RE_VEX_EPOCH = re.compile(_RE_VEX_EPOCH)

def find_files(
	all_paths: Iterable[Iterable[str]], valid: Callable[[str], bool]
) -> Iterable[str]:
	'''Find first set of matching files in a set of path lists

	* `all_paths` is the return from `find_all_files()`
	'''
	for paths in all_paths:
		if paths := [p for p in paths if valid(p)]:
			return paths
	return []

def find_all_files(
	paths: Iterable[str] = ('.',), _visited: Set[Tuple[int, int]] = None
) -> Iterable[Iterable[str]]:
	'''Find all unique files inside the listed paths'''
	blacklist = '|'.join(sorted(BLACKLIST, reverse=True))
	blacklist = re.compile(rf'(?:^|\b|\d|_)(?:{blacklist})(?:$|\b|\d|_)').search
	all_paths, these, visited = [], [], set() if _visited is None else _visited
	for path in paths:
		# stat path
		try:
			st = os.stat(path)
		except OSError:
			vlbi.error(f'stat failed: {shlex.quote(path)}')
			continue
		# check for duplicate
		if (st.st_dev, st.st_ino) in visited:
			continue
		visited.add((st.st_dev, st.st_ino))
		# regular file
		if stat.S_ISREG(st.st_mode):
			these.append(path)
		# directory
		elif stat.S_ISDIR(st.st_mode):
			if these:
				all_paths.append(these)
				these = []
			try:
				ls = sorted(f for f in os.listdir(path) if not blacklist(f))
				ls = [os.path.join(path, f) for f in ls]
			except OSError:
				vlbi.error(f'list dir failed: {shlex.quote(path)}')
				continue
			ls = [j for i in find_all_files(ls, visited) for j in i]
			all_paths.append(ls)
	if these:
		all_paths.append(these)
	return all_paths

def to_value(text: str, string_only: bool = False) -> Union[
	datetime, float, int, str, Iterable[Union[datetime, float, int, str]]
]:
	'''Convert string to datetime, int, or float if possible'''
	text = (text or '').strip()
	if text is None or text in '-':  # empty
		return None
	if ',' in text or '\n' in text:  # list
		return [to_value(i, string_only) for i in _RE_VALUE_SEP.split(text)]
	if string_only:  # a priori string
		return text or None
	try:  # datetime
		return vlbi.datetime(text)
	except (ValueError, OverflowError):
		try:  # percent, float
			return 0.01 * float(text[:-1]) if text[-1:] == '%' else float(text)
		except (TypeError, ValueError, AttributeError):
			return text  # string of last resort

def read_path(path: str) -> str:
	'''Read a text file'''
	with open(path) as f:
		return f.read()

class ReportText(NamedTuple):
	'''Report filename and text'''
	filename: str
	text: str

def read_report_text(path: str, keep_looking_for_name=True) -> ReportText:
	'''Read report filename and text from a .rpt, .txt, .corr, or .tgz file'''
	if path.lower().endswith(('.tgz', '.tar.gz', '.tar.bz')):
		with tarfile.open(path) as tarball:
			text = filename = None
			for info in tarball:
				if info.isreg():
					if info.path.lower().endswith('.hist'):
						with tarball.extractfile(info) as file:
							if r := _IS_REPORT_CONTENT(file.read()):
								text = r[0].decode(errors='replace')
					elif info.path.lower().endswith('.wrp'):
						with tarball.extractfile(info) as file:
							if r:= _IS_SESSION_LINE(file.read()):
								filename = r[1].decode(errors='replace').lower()
				if (filename or not keep_looking_for_name) and text is not None:
					return ReportText((filename or 'report') + '.corr', text)
		if text is None:
			raise FileNotFoundError(errno.ENOENT, 'No report found', path)
	else:
		text = read_path(path)
	filename = os.path.basename(path).lower()
	return (_RE_REPORT_EXTS.sub('', filename) or filename) + '.corr', text

class EOP(NamedTuple):
	'''Earth Orientation Parameters'''
	mjd: int
	tai_utc: float
	ut1_utc: float
	xpole: float
	ypole: float

class Calcs(NamedTuple):
	'''Details from DiFX calc file(s)'''
	software: Set[str]
	version: Set[str]
	eop: Iterable[EOP]

	_re = re.compile(r'''
		^(?:
			difx\s*(version|label) |
			eop\s+(\d+)\s*(time|tai_utc|ut1_utc|xpole|ypole)\s*\([^\)]*\)
		)\s*:(.*)
	''', re.I | re.M | re.X)
	_re_difx = re.compile(r'^[\ ._-]+|[\ ._-]+$|[\ ._-]*difx[\ ._-]*', re.I)

	@classmethod
	def read(cls, paths: Iterable[str], verbose: bool = False) -> 'Calcs':
		'''Read DiFX input file(s) or dir(s)'''
		paths, sw, ver, eop = list(paths), set(), set(), set()
		i, n = 0, len(paths)
		for i, path in enumerate(paths, 1):
			vlbi.progress(f'reading {i} / {n} calc files', bar=(i / n))
			eops = {}
			for t_ver, n_eop, t_eop, v in cls._re.findall(read_path(path)):
				if t_ver:
					sw.add('DiFX')
					ver.add('-'.join(cls._re_difx.sub('', v.lower()).split()))
				else:
					eops.setdefault(int(n_eop), {})[t_eop.lower()] = float(v)
			required = {'time', 'tai_utc', 'ut1_utc', 'xpole', 'ypole'}
			eop.update(EOP(
				int(i['time']), i['tai_utc'], i['ut1_utc'],
				i['xpole'], i['ypole']
			) for i in eops.values() if not (required - set(i)))
		vlbi.progress()
		vlbi.info(f'reading {i} / {n} calc files', verbose)
		return cls(sw, ver, sorted(eop))

class InputClocks(NamedTuple):
	'''Clock info from a DiFX input file'''
	start: datetime
	offset: float
	rate: float

class Inputs(NamedTuple):
	'''DiFX input file details'''
	tint: Set[float]
	nchan: Set[int]
	fftspecres: Set[float]  # MHz
	specres: Set[float]  # MHz
	clocks: Mapping[str, InputClocks]

	_re_comment = re.compile(r'^\s*[#@].*')
	_re_part = re.compile(r'((?!\b\d+\b)\w+(?:\s+(?!\d+\b)\w+)*)|(\b\d+\b)')

	@classmethod
	def read(cls, paths: Iterable[str], verbose: bool = False) -> 'Inputs':
		'''Read details from DiFX input files'''
		paths, data = list(paths), {}
		i, n = 0, len(paths)
		for i, path in enumerate(paths, 1):
			vlbi.progress(f'reading {i} / {n} input files', bar=(i / n))
			for line in cls._re_comment.sub('', read_path(path)).splitlines():
				key, _, value = line.partition(':')
				if (key := key.strip()) and (value := value.strip()):
					keys = []
					for k, j in cls._re_part.findall(key):
						k = ' '.join(k.lower().split()) if k else int(j)
						keys.append(k)
					container = data
					for key in keys[:-1]:
						container = container.setdefault(key, {})
					container[keys[-1]] = value.lower()
		vlbi.progress()
		vlbi.info(f'reading {i} / {n} input files', verbose)
		tint = float(data['int time']['sec'])
		nchan = {i: int(v) for i, v in data['num channels'].items()}
		fftspecres = {
			i: float(data['bw']['mhz'][i]) / n for i, n in nchan.items()
		}
		specres = {
			f * int(data['chans to avg'][i]) for i, f in fftspecres.items()
		}
		nchan = set(nchan.values())
		fftspecres = set(fftspecres.values())
		clocks = {}
		for i, station in data['telescope name'].items():
			start = vlbi.mjd2datetime(float(data['clock ref mjd'][i]))
			offset = float(data['clock coeff'][i][0]) / -1e6
			rate = float(data['clock coeff'][i][1]) / -1e6
			clocks[station.capitalize()] = InputClocks(start, offset, rate)
		return cls(tint, nchan, fftspecres, specres, clocks)

def read_passwd(analyst: str = None) -> str:
	'''Read analyst name from passwd file if not provided'''
	if not analyst:
		pw = pwd.getpwuid(os.getuid())
		for name in (pw.pw_gecos, pw.pw_name):
			n, name = 1, name.replace('_', ' ').strip()
			while n:
				name, n = _RE_PASSWD_PARENS.subn(' ', name)
			name = ' '.join(name.split())
			if name:
				return name
	return analyst

def vex_epoch(text: str) -> datetime:
	'''Convert date from `####y###d##h##m##s` format to `datetime`'''
	if r := _RE_VEX_EPOCH.match(text):
		y, d, h, m, s = [int(i or 0) for i in r.groups()]
		try:
			return datetime(y, 1, 1, h, m, s) + timedelta(d - 1)
		except (ValueError, OverflowError):
			pass
	return datetime.max

def vex_sec(text: str) -> float:
	'''Convert value from `1.5 usec` format to number of seconds'''
	try:
		num, units = (text.split() + ['', ''])[:2]
		return float(num) * _VEX_UNITS[units.lower()]
	except KeyError as e:
		raise ValueError from e

def vex_props(text: str) -> Mapping[str, Iterable[str]]:
	'''Maps vex properties from a def/scan block text'''
	atts = {}
	for line in text.split(';'):
		k, _, v = line.partition('=')
		if k := k.strip():
			atts.setdefault(k, []).append([i.strip() for i in v.split(':')])
	return atts

class ClockEarly(NamedTuple):
	'''VEX clock_early'''
	valid: datetime
	offset: float
	measured: datetime
	rate: float

class VEXScan(NamedTuple):
	'''VEX scan'''
	start: datetime
	source: str
	stations: Mapping[str, bool]
	'''`{'Kk': True, 'Wz': False}` == Kk was correlated and Wz wasn't'''

class VEX(NamedTuple):
	'''VEX file content'''
	session: str
	clocks: Mapping[str, ClockEarly]
	scans: Mapping[str, VEXScan]

	_r = r'(?:^|(?<=;))\s*\$([A-Z0-9_-]+)\s*;((?:(?!\s*\$)[^;]*;)*)'
	_re_section = re.compile(_r)
	_r = r'(?:^|(?<=;))\s*(def|scan)\s*([^;]*);((?:(?!\s*end\1\s*;)[^;]*;)*)'
	_re_def = re.compile(_r)

	@classmethod
	def read(cls, path: str, rootfile: bool = False) -> 'VEX':
		'''Read details from HOPS root (VEX) file'''
		# pre-process VEX (one statement per line, no comments
		f = re.sub(r'\*.*', '', read_path(path))
		f = f.replace('\n', ' ').replace(';', ';\n')
		f = {r[1].upper(): r[2] for r in cls._re_section.finditer(f)}
		# session name
		session = vex_props(f.get('EXPER', {})).get('exper_name', [])
		session = {i[0] for i in session if i and i[0]}
		session = (max(session).upper() if session else '') or None
		# extract clocks
		clocks = {}
		for def_scan, station, text in cls._re_def.findall(f.get('CLOCK', '')):
			if def_scan == 'def' and len(station := station.strip()) == 2:
				for v in vex_props(text)['clock_early']:
					v += ['', '']
					valid, offset = vex_epoch(v[0]), vex_sec(v[1])
					meas = vex_epoch(v[2]) if v[2].strip() else valid
					rate = float(v[3].strip() or 0)
					if not LOW_DATE < meas < HIGH_DATE:
						continue
					v = ClockEarly(valid, offset, meas, rate)
					clocks.setdefault(station, []).append(v)
		# extract start time and station participation/correlation
		scans = {}
		for r in () if rootfile else cls._re_def.finditer(f['SCHED']):
			props = vex_props(r[3])
			if 'start' in props and 'station' in props:
				start = vex_epoch(props['start'][0][0].strip())
				src = props['source'][0][0].strip()
				stations = {}
				for i in props['station']:
					v = int((i[6:] or ['1'])[0].strip() or 1) > 0
					stations[i[0].strip()] = v
				scans[r[2].strip()] = VEXScan(start, src, stations)
		# return results
		return cls(session, clocks, scans)

class HOPSChan(NamedTuple):
	'''Channel details from HOPS fringe file'''
	freq: float
	name: str
	mk4: str

class FringeClock(NamedTuple):
	'''Clock info from a fringe record'''
	time: datetime
	offset: float
	rate: float

class Scan(NamedTuple):
	'''Source, start time, baseline, and frequency band'''
	source: str
	start: datetime
	bl: Set[str]
	band: str

class Fringe(NamedTuple):
	'''Properties from a fringe record'''
	session: str
	scan: str
	source: str
	start: datetime
	length: timedelta
	time: datetime
	qcode: int
	error: str
	b: str
	bl: Tuple[str, str]
	baseline: Tuple[str, str]
	clock: Tuple[float, float]
	rate: Tuple[float, float]
	chans: Set[HOPSChan]
	man_pcal: Tuple[bool, bool]
	cf: str
	bands: Set[str]
	all_bands: Set[str]
	snr: float
	ff_version: str
	corr_sw: str

	_r = rb'\bfourfit\s*(\d+(?:[.\d]*\d)?)\s*r(?:ev(?:ision)?)?\s*(\d+)?'
	_re_version = re.compile(_r, re.I)

	@classmethod
	def read(cls, path: str, ff_version: bool = False) -> 'Fringe':
		'''Read details from a Mk4 fringe file'''
		if ff_version:
			recs = 200, 201, 202, 203, 205, 207, 208, 221, 222
		else:
			recs = 200, 201, 202, 203, 205, 207, 208, 222
		required = set(recs) - {221, 222}
		r = vlbi.mk4.records(path, recs, True, False)
		if missing := required - set(r):
			missing = ', '.join(map(str, sorted(missing)))
			vlbi.error(f'record {missing} found: {shlex.quote(path)}')
			raise ValueError(f'record {missing} found: {shlex.quote(path)}')
		# fourfit version number
		ff_ver = None
		if ff_version and 221 in r:
			if v := cls._re_version.search(vlbi.mk4.get_ps(r)):
				ff_ver = v[1].decode() + ('-' + v[2].decode() if v[2] else '')
		# chans
		chans, ff_chans = set(), {}
		for v in r[205]['ffit_chans']:
			if ch := v['id'].strip():
				ff_chans.update((i, ch) for i in v['chans'] if i >= 0)
		for i, v in enumerate(r[203]['channels']):
			if v['index'] >= 0:
				for j in (0, 1):
					id = v['chan_id'][j].decode()
					mk4 = ff_chans.get(i, b'').decode() or None
					chans.add(HOPSChan(float(v['freq'][j]), id, mk4))
		# pcal
		if 'pc_mode' in r[207].dtype.fields:
			pc_modes = int(r[207]['pc_mode'])
			man_pcal = pc_modes // 10 % 10 == 3, pc_modes % 10 == 3
		else:
			pc = r[207]['pc_amp']
			pc = (pc == numpy.zeros_like(pc)) | (pc == numpy.ones_like(pc))
			man_pcal = bool(pc[0].all()), bool(pc[1].all())
		# times
		start = vlbi.mk4.dtype2datetime(r[200]['scan_time'])
		length = timedelta(0, float(r[200]['stop_offset']))
		# correlation details
		corr_sw = r[200]['corr_name'].decode()
		# results
		return Fringe(
			r[200]['experiment_name'].decode(),
			r[200]['scan_name'].decode(),
			r[201]['source'].decode(),
			start, length, start + length / 2,
			int(r[208]['quality']),
			r[208]['errcode'].decode().strip(),
			r[202]['baseline'].decode(),
			tuple(i.decode() for i in r[202]['station_id']),
			tuple(i.decode() for i in r[202]['station_name']),
			list(map(float, r[202]['clock'])),
			list(map(float, r[202]['clock_rate'])),
			chans, man_pcal, r[222]['cf'].decode() if 222 in r else None,
			set(name[:1] for _, name, id in chans if id and name[:1]),
			set(name[:1] for _, name, _ in chans if name[:1]),
			float(r[208]['snr']), ff_ver,
			SOFTWARE_NAME_MAP.get(corr_sw.lower(), corr_sw)
		)

class Fringes(NamedTuple):
	'''Properties of a set of fringe records'''
	session: str
	chans: Iterable[HOPSChan]
	station_chans: Mapping[str, Set[str]]
	stations: Mapping[str, vlbi.stations.Station]
	man_pcals: Mapping[str, bool]
	clocks: Mapping[str, Set[FringeClock]]
	scans: Mapping[Scan, Fringe]
	bands: Iterable[str]
	cf: Set[str]
	ff_version: str
	corr_sw: Set[str]

	is_fringe_path = re.compile(r'(?:.*/)?[a-zA-Z0-9_]{2}\.[A-Z]\.').match

	@classmethod
	def read(cls, paths: Iterable[str], verbose=False) -> 'Fringes':
		'''Read from Mk4 fringe file paths'''
		# read fringes
		ff, i, n, ff_ver = [], 0, len(paths), None
		for i, path in enumerate(paths, 1):
			vlbi.progress(f'reading {i} / {n} fringe files', bar=(i / n))
			try:
				ff.append(v := Fringe.read(path, not ff_ver))
				ff_ver = ff_ver or v.ff_version
			except (OSError, ValueError):
				vlbi.warn(f'can\'t read {shlex.quote(path)}')
				continue
		vlbi.progress()
		vlbi.info(f'reading {i} / {n} fringe files', verbose)
		# merge chans and stations
		raw_chans, station_chans, stations, man_pcal = set(), {}, {}, {}
		for f in ff:
			raw_chans.update(f.chans)
			chan_names = [v[1] for v in f.chans]
			for i in (0, 1):
				id = f.bl[i]
				station_chans.setdefault(id, set()).update(chan_names)
				s = vlbi.stations.Station(id, f.baseline[i], mk4id=f.b[i])
				stations[id] = s
				for st, mpc in zip(f.bl, f.man_pcal):
					man_pcal[st] = max(mpc, man_pcal.get(st, False))
		# associate chan freq/name with 1-char ID
		chans_map = {}
		for freq, name, id in raw_chans:
			ids = chans_map.setdefault((freq, name), set())
			if id is not None:
				ids.add(id)
		# remove duplicates with `None` 1-char ID
		chans = []
		for (freq, name), ids in chans_map.items():
			for id in (ids if ids else [None]):
				chans.append(HOPSChan(freq, name, id))
		chans.sort()
		# add +/- (upper/lower) sideband decorators to 1-char IDs
		for i, ((f0, n0, c0), (f1, n1, c1)) in enumerate(zip(chans, chans[1:])):
			if f0 == f1 and c0 and c0 == c1:
				if (n0[-2:-1] + n1[-2:-1]).upper() == 'LU':
					chans[i] = HOPSChan(f0, n0, c0 + '-')
					chans[i + 1] = HOPSChan(f1, n1, c1 + '+')
		# merge clocks
		clocks = {}
		for f in ff:
			for station, offset, rate in zip(f.bl, f.clock, f.rate):
				clock = FringeClock(f.time, offset, rate)
				clocks.setdefault(station, set()).add(clock)
		# return results
		return cls(
			max(f.session for f in ff),
			chans, station_chans, stations, man_pcal, clocks,
			{
				Scan(f.source, f.start, frozenset(f.bl), b): f
				for f in ff for b in f.bands
			},
			sorted({i for f in ff for i in f.all_bands}),
			{f.cf for f in ff if f.cf},
			ff_ver, {f.corr_sw for f in ff}
		)

def read_snr(
	snr_path: str = None, skd_path: str = None, verbose: bool = True
) -> Mapping[Scan, float]:
	'''Return (str) snratio file output from (str) skd file path and mk4.'''
	# read existing *.snr file
	if snr_path:
		vlbi.info(f'reading {snr_path}', verbose)
		f = read_path(snr_path).splitlines()
	# convert *.skd file to snr file
	elif skd_path:
		vlbi.info(f'generating snr: sked {shlex.quote(skd_path)}', verbose)
		skd_path0 = os.path.realpath(skd_path)
		with vlbi.cd():
			try:
				if code := run(
					['sked', skd_path0],stdout=DEVNULL, stderr=DEVNULL,
					input=b'unit snr\nxl snr\nlist\nunit screen\nquit\n'
				).returncode:
					raise OSError(code, 'internal sked error')
				f = read_path('snr').splitlines()
			except OSError as e:
				vlbi.error(f'sked error: {e}: continuing without snr')
				return {}
	else:
		return {}
	# parse snr file
	snrs = {}
	try:
		# snr headers
		freqs = next(line for line in f if line.startswith('Source'))
		freqs = (i.strip() for i in freqs.rpartition(' for ')[2].split(','))
		freqs = list(filter(None, freqs))
		heads = next(line for line in f if line.startswith('name '))
		heads = [i.split('-') for i in heads.split() if '-' in i][1:]
		col_per = len(heads) // len(freqs)
		baselines, bands, blbs, cols = {}, {}, {}, []
		for i, (a, b) in enumerate(heads):
			band = freqs[i // col_per]
			baselines[frozenset({a, b})] = a, b
			bands[band] = None
			cols.append((frozenset({a, b}), band))
			blbs[cols[-1]] = None
		# snr data
		for line in (line for line in f if '|' in line):
			line = line.split('|')
			source, start = line[0].split()
			start = datetime.strptime(start, '%y%j-%H%M%S')
			data = [s[i:(i + 6)] for s in line[2:] for i in range(0, len(s), 6)]
			data = [float(i) or 1.0 if i.strip() else None for i in data]
			for i, x in enumerate(data):
				if x:
					snrs[Scan(source, start, *cols[i])] = x
	except:
		vlbi.error('snr file syntax error, continuing without snr')
		return {}
	return snrs

class BaselineBand(NamedTuple):
	'''Baseline and band'''
	s1: str  # first station
	s2: str  # second station
	band: str

class SNRRatio(NamedTuple):
	'''Ratio of observed SNR to predicted SNR, and average point count'''
	ratio: float
	n: int

def snr_ratios(
	scans: Mapping[Scan, Fringe], snr: Mapping[Scan, float],
	verbose: bool = False
) -> Mapping[BaselineBand, SNRRatio]:
	'''Calculate SNR ratios'''
	# map observed SNR to predicted SNR
	ratios, counts = {}, {}
	for s, f in scans.items():
		bl = '-'.join(f.bl)
		name = f'{f.scan} {bl}:{s.band}'
		# filter out errors and extreme SNR
		if not f.qcode or (f.error and f.error.upper() in 'BEF'):
			q = f.error.upper() if f.qcode else '0'
			vlbi.info(f'snr: skipped {name} ({q} code)', verbose)
			continue
		if not (snr0 := snr.get(s)):
			msg = f'snr: skipped {name} (no prediction)'
			vlbi.info(msg, verbose)
			continue
		if not 6.9 < f.snr < 5000:
			msg = ' >= 5000' if f.snr >= 5000 else ' <= 6.9'
			vlbi.info(f'snr: skipped {name}: snr = {f.snr:0.2f}{msg}', verbose)
			continue
		# calculate and filter ratio of observed SNR / predicted SNR
		if (ratio := f.snr / snr0) > 5:
			msg = 'snr: skipped' if ratio > 10 else 'caution:'
			vlbi.info(f'{msg} {name} (high snr ratio {ratio:0.2f})', verbose)
			if ratio > 10:
				continue
		# sum
		ratios[(*f.bl, s.band)] = ratios.get((*f.bl, s.band), 0.0) + ratio
		counts[(*f.bl, s.band)] = counts.get((*f.bl, s.band), 0) + 1
	# divide for ratios
	return {
		BaselineBand(*blb): SNRRatio(total / counts[blb], counts[blb])
		for blb, total in ratios.items()
	}

class Table(pandas.DataFrame):
	'''Pandas DataFrame with extra `read` and `legend` properties'''

	legend = {}  # class default tells pandas `legend` is a property (not a col)

	def __init__(self, *args, legend: Mapping[str, str] = (), **kwargs):
		super().__init__(*args, **kwargs)
		self.legend = dict(legend)

	def __str__(self) -> str:
		heads, cols = [], []
		for name in self:
			col, width = self[name], len(name)
			if col.size:
				# datetime column
				if numpy.issubdtype(col.dtype, numpy.datetime64):
					col = [v.to_pydatetime() for v in col]
					if any(v.second or v.microsecond for v in col):
						fmt, width = '%Y-%j-%H%M%S', max(width, 15)
					else:
						fmt, width = '%Y-%j-%H%M', max(width, 13)
					cols.append([v.strftime(fmt) for v in col])
					heads.append(f'{name:>{width}}')
				# string column
				elif numpy.issubdtype(col.dtype, object):
					col = ['-' if i is None else i for i in col]
					width = max(width, max(map(len, col)))
					cols.append([f'{v:{width}}' for v in col])
					heads.append(f'{name:{width}}')
				# numeric column
				else:
					# find NaNs
					nans = numpy.where(col != col)[0]
					# convert to str (pandas lines up decimals, but adds indent)
					col = textwrap.dedent(col.to_string(index=0)).splitlines()
					width = max(width, max(map(len, col)))
					col = [f'{v:>{width}}' for v in col]
					# replace NaN with -
					if nans.size:
						# put - where the . is
						n = max(len(v.partition('.')[2]) for v in col)
						nan_str = ' ' * (width - n - 1) + '-' + ' ' * n
						for i in nans:
							col[i] = nan_str
					cols.append(col)
					heads.append(f'{name:>{width}}')
			else:
				cols.append([])
				heads.append(name)
		head = ' '.join(heads)
		cols = ''.join(' '.join(row) + '\n' for row in zip(*cols))
		text = head + '\n' + '-' * len(head) + '\n' + cols + self._legend_text()
		return _RE_TRAILING_WS.sub('', text)

	def _legend_text(self) -> str:
		'''Get legend text'''
		if not self.legend:
			return ''
		width = max(map(len, self.legend))
		v = ''.join(f'* {k:{width}}  {v}\n' for k, v in self.legend.items())
		return '\n' + v

	def __repr__(self) -> str:
		return super().__repr__() + '\n' + self._legend_text()

	@classmethod
	def read(cls, text: str) -> 'Table':
		'''Read table section from it's text content'''
		# split into lines
		lines = _RE_READ_STRIP.sub('', text).splitlines()
		# extract column names
		names = lines.pop(0).split()
		# split into columns
		n, cols = len(names), [[] for _ in names]
		for line in lines:
			line = line.split(None, len(names) - 1)
			for i, v in enumerate(line + ['-'] * (n - len(line))):
				cols[i].append(v)
		# parse columns
		for i, col in enumerate(cols):
			if all(map(_RE_DATETIME.match, col)):
				cols[i] = [None if v in '-' else (
					datetime(int(v[:4]), 1, 1) + timedelta(
						days=int(v[5:8]) - 1, hours=int(v[9:11]),
						minutes=int(v[11:13]), seconds=int(v[13:15] or 0)
					)
				) for v in col]
			else:
				try:
					cols[i] = list(map(int, col))
				except ValueError:
					try:
						cols[i] = [None if v in '-' else float(v) for v in col]
					except ValueError:
						pass
		# compile data
		data = {name: col for name, col in zip(names, cols)}
		legend = {k: v.strip() for k, v in _RE_LEGEND.findall(text)}
		return cls(data=data, legend=legend)

class StationDetails(NamedTuple):
	'''Details collected about a station'''
	id: str
	name: str
	mk4: str
	in_vex: bool
	in_fringes: bool
	in_master: bool
	participating: bool
	notes: Iterable[str]

class Report(collections.abc.MutableMapping):
	'''IVS Correlator Report v2.0

	* `source` is either a path to a report file, or a map of the content
	'''

	def __init__(self, source: Union[str, io.IOBase, Mapping] = ()):
		self._data = {}
		if isinstance(source, str):
			self.read(source)
		else:
			self._data = {k.upper(): v for k, v in dict(source).items()}

	@staticmethod
	def _timerange(
		t0: datetime, t1: datetime, start: datetime, end: datetime
	) -> str:
		'''Pretty-format a time range'''
		if t0 == t1:
			return f'{t0:%Y-%j-%H%M%S}'
		msg = '(start) ' if t0 == start else ''
		msg += f'{t0:%Y-%j-%H%M%S} -- {t1:%Y-%j-%H%M%S}'
		return msg + (' (end)' if t1 == end else '')

	@classmethod
	def read(cls, path: Union[str, io.IOBase]) -> 'Report':
		'''Read a report from file'''
		# read file
		if isinstance(path, (str, bytes)):
			with open(path) as f:
				full_text = f.read()
		else:
			full_text = path.read()
		return cls.reads(full_text)

	@classmethod
	def reads(cls, text: str) -> 'Report':
		'''Read a report from text'''
		result = cls()
		# split into sections
		for section, text in _RE_READ_SECTION.findall(text):
			# text
			if section.endswith('FILE') or section.endswith('TEXT'):
				result[section] = text
			# table
			elif _RE_READ_DASHES.search(text):
				result[section] = Table.read(text)
			# dictionary
			else:
				pre, post = {}, result.setdefault(section, {})
				for line in _RE_READ_STRIP.sub('', text).splitlines():
					k, _, v = line.strip().partition(' ')
					k = k.strip()
					pre[k] = pre.get(k, '') + v.strip() + '\n'
				for k, v in pre.items():
					string_only = 'TEXT' in k or 'STRING' in k or 'VERSION' in k
					post[k] = to_value(v, string_only)
		return result

	@classmethod
	def build(
		cls, paths: Iterable[str] = ('.',), *,
		correlator: str = None,
		analyst: str = None,
		version: str = '1-1',
		ref: str = None,
		catmap: Union[str, bool] = True,
		corr_patch: str = '', corr_sw: str = '', corr_version: str = '',
		fringe_patch: str = '', fringe_sw: str = '', fringe_version: str = '',
		vgosdb_patch: str = '', vgosdb_sw: str = '', vgosdb_version: str = '',
		verbose: bool = False
	) -> 'Report':
		'''Build a new report from correlation files and directories

		* `paths` contains files and directories to use
		'''
		## find files
		all_paths = find_all_files(paths)
		## read VEX and OVEX
		session, vex_scans, vex_clocks = None, {}, {}
		for path in (
			find_files(all_paths, _IS_OVEX) + find_files(all_paths, _IS_VEX)
		):
			vlbi.info(f'reading {shlex.quote(path)}', verbose)
			vex = VEX.read(path)
			if not path.lower().endswith('.ovex'):
				for station, clocks in vex.clocks.items():
					vex_clocks.setdefault(station, set()).update(clocks)
			for name, scan in vex.scans.items():
				if name in vex_scans:
					ss = vex_scans[name].stations
					for station, observed in scan.stations.items():
						# -1 in OVEX supersedes missing or +1 in VEX
						ss[station] = ss.get(station, True) and observed
				else:
					vex_scans[name] = scan
			session = vex.session or session
		if not session:
			vlbi.error('No session name from VEX file', exit=errno.ENOENT)
		## read master file
		master = vlbi.master.get_session(
			session, *find_files(all_paths, _IS_MASTER),
			near=next(iter(vex_scans.values())).start.year
		)
		## read catmap
		if catmap in (True, None):
			catmap = vlbi.catmap.CatMap(verbose=verbose)
		else:
			catmap = vlbi.catmap.CatMap(catmap or (), verbose=verbose)
		## read input files
		if input_files := find_files(all_paths, _IS_INPUT):
			inputs = Inputs.read(input_files, verbose)
		else:
			vlbi.error('no input files found, skipping part of CORRELATOR')
		## read calc files
		if calc_files := find_files(all_paths, _IS_CALC):
			calcs = Calcs.read(calc_files, verbose)
		else:
			vlbi.error('no calc files found, skipping EOP, part of CORRELATOR')
		## read fringes
		fringes = Fringes.read(find_files(all_paths, _IS_ROOT_PATH), verbose)
		## compile stations list
		vex_stations = {
			id for scan in vex_scans.values() for id in scan.stations
		}
		all_stations = find_files(all_paths, _IS_STATIONS)
		all_stations = vlbi.stations.Stations(*all_stations, verbose=verbose)
		stations = {}
		for id in set(master.stations) | set(fringes.stations) | vex_stations:
			name = mk4 = None
			if id in fringes.stations:
				st = fringes.stations[id]
				name, mk4 = st.name, st.mk4id
			elif id in all_stations:
				st = all_stations[id]
				name, mk4 = st.name, st.mk4id
			stations[id] = StationDetails(
				id, name, mk4, id in vex_stations, id in fringes.stations,
				id in master.stations, master.stations.get(id), []
			)
		## read logs
		regex = re.escape(session)
		regex = rf'(?:.*/?){session}[.-_]?[a-z0-9$%]{{2}}\.log$'
		regex = re.compile(regex, re.I).match
		fits = vlbi.fit_fmout.fit_fmout(
			find_files(all_paths, regex), epoch=master.datetime,
			clock_offsets=None, verbose=verbose
		).fits
		## compile qcodes
		qcodes = {}
		# set - (minus) or N (not correlated) default from VEX files
		for scan in vex_scans.values():
			for bl in itertools.combinations(scan.stations, 2):
				for band in fringes.bands:
					code = all(scan.stations[id] for id in bl)
					q = 'N' if code else '-'
					qcodes[(scan.start, scan.source, frozenset(bl), band)] = q
		# set final code from fringes
		for i in fringes.scans.values():
			for band in i.bands:
				code = (i.error or str(i.qcode)) if i.qcode else '0'
				qcodes[(i.start, i.source, frozenset(i.bl), band)] = code
		## compile SNR ratios
		snrr = None
		if snr_path := find_files(all_paths, _IS_SNR):
			snrs = read_snr(snr_path=snr_path[0], verbose=verbose)
			snrr = snr_ratios(fringes.scans, snrs, verbose)
		elif skd_path := find_files(all_paths, _IS_SKD):
			snrs = read_snr(skd_path=skd_path[0], verbose=verbose)
			snrr = snr_ratios(fringes.scans, snrs, verbose)
		else:
			vlbi.error('No SKD or SNR file found, skipping SNR_RATIOS')
		## sort baselines by fringe order (secondary), then ID name (primary)
		baselines = {}
		for bl in itertools.combinations(sorted(stations), 2):
			baselines[frozenset(bl)] = tuple(sorted(bl))
		for fringe in fringes.scans.values():
			baselines[frozenset(fringe.bl)] = fringe.bl
		# sort by tuples since frozenset doesn't retain order
		baselines = sorted((tup, fset) for fset, tup in baselines.items())
		baselines = {fset: tup for tup, fset in baselines}
		## compile dropped channels
		used, drop_chans = {}, {}
		for scan, fringe in fringes.scans.items():
			use = used.setdefault(scan.bl, set())
			use.update(i.name for i in fringe.chans if i.mk4)
		chans = {i.name for i in fringes.chans}
		dropped = {bl: chans - used for bl, used in used.items()}
		# find station-wide channel drops
		for station in fringes.stations:
			for chan in fringes.chans:
				if all(
					station not in bl or chan.name in ch
					for bl, ch in dropped.items()
				):
					drop_chans.setdefault(station, set()).add(chan.name)
		# find baseline-wide channel drops
		for bl_fset, bl_tup in baselines.items():
			bl_name = '-'.join(bl_tup)
			for chan in dropped.get(bl_fset, ()):
				if all(
					chan not in drop_chans.get(id, ()) for id in bl_tup
				):
					drop_chans.setdefault(bl_name, set()).add(chan)
		## HEADER
		sections = {'HEADER': {
			'SESSION': session,
			'VGOSDB': master.db,
			'START': master.datetime,
			'END': master.datetime + master.duration,
			'CORRELATOR': correlator or master.correlator,
			'ANALYST': read_passwd(analyst),
			'VERSION': version
		}}
		## SUMMARY
		qc = list(qcodes.values())
		n, m = len(qcodes), sum(1 for i in qc if i not in '-N')
		n_59 = sum(1 for i in qc if i in '56789')
		n_0 = sum(1 for i in qc if i == '0')
		n_14an = sum(1 for i in qc if i not in '0-56789')
		n_14ah = sum(1 for i in qc if i not in '0-56789N')
		n_min = n - n_59 - n_0 - n_14an
		sections['SUMMARY'] = Table(data={
			'qcode': ['5-9', '0', '1-4,A-H,N', 'removed'],
			'total': [f'{i / n:7.2%}' for i in [n_59, n_0, n_14an, n_min]],
			'correlated': [f'{i / m:10.2%}' for i in [n_59, n_0, n_14ah, 0]]
		}, legend={
			'qcode': 'quality codes, error codes, or status',
			'total': 'percent of total scans',
			'correlated': 'percent of correlated scans'
		})
		## STATIONS
		ss = sorted(stations.values())
		for i in ss:
			if not i.name:
				msg = f'missing name for station {i.id}'
				msg += ' (do you you need an ns-codes.txt file?)'
				vlbi.warn(msg)
			if not i.mk4:
				msg = f'missing Mark4 ID for station {i.id}'
				msg += ' (do you you need an m.stations or stations.m file?)'
				vlbi.warn(msg)
		sections['STATIONS'] = Table(data={
			'station': [i.id for i in ss],
			'name': [i.name and catmap.stn[i.name] for i in ss],
			'mk4': [i.mk4 for i in ss]
		}, legend={
			'station': '2-char station ID',
			'name': '3- to 8-char station name',
			'mk4': '1-char HOPS station code'
		})
		## NOTES
		notes = []
		code2sort = '987654321ABCDEFGHIJKLMOPQRSTUVWXYZ0-N'
		code2sort = {c: i for i, c in enumerate(code2sort)}
		for s in ss:
			if s.id not in master.stations:
				notes.append((s.id, 'Not in master file'))
			elif not master.stations[s.id]:
				if s.id in fringes.stations:
					msg = 'Observed despite non-observing status in master file'
				else:
					msg = 'Did not observe'
				notes.append((s.id, msg))
				continue
			# more qcode sorting
			q0, interest0 = '_', float('inf')
			for (t, _, bl, band), q in qcodes.items():
				if s.id in bl:
					interest = {'0': 1, 'N': 2, '-': 3}.get(q, 0)
					if interest < interest0:
						interest0 = interest
						q0 = q
			if (msg := {
				'-': 'Minused out',
				'N': 'Not correlated',
				'0': 'No fringes found'
			}.get(q0)):
				notes.append((s.id, msg))
			elif scans := sorted(
				scan for scan in vex_scans.values() if s.id in scan.stations
			):
				start, end = scans[0].start, scans[-1].start
				t0, t1, minus0, ranges = None, None, False, []
				for scan in scans:
					if (minus := not scan.stations[s.id]) != minus0:
						if minus0:
							ranges.append(cls._timerange(t0, t1, start, end))
						t0, minus0 = scan.start, minus
					t1 = scan.start
				if minus:
					ranges.append(cls._timerange(t0, t1, start, end))
				if ranges:
					notes.append((s.id, 'Minused out: ' + ', '.join(ranges)))
			if fringes.man_pcals.get(s.id):
				notes.append((s.id, 'Applied manual phase calibration'))
			if s.id in drop_chans:
				msg = 'Removed channel from fringe fitting: '
				notes.append((s.id, msg + ', '.join(drop_chans[s.id])))
			if s.id in vex_clocks and len(cc := sorted(vex_clocks[s.id])) > 1:
				for i in range(len(cc) - 1):
					dt = f'{1e6 * (cc[i + 1].offset - cc[i].offset):0.3f} usec'
					t = f'{cc[i + 1].valid:%Y-%j-%H%M%S}'
					notes.append((s.id, f'Clock break at {t} ({dt})'))
		for bl in baselines.values():
			if (key := '-'.join(bl)) in drop_chans:
				msg = 'Removed channel from fringe fitting: '
				notes.append((key, msg + ', '.join(drop_chans[key])))
		# use "plain text" to flavor with spaces between stations
		# this will still be treated as a Table when read
		if not notes:
			notes.append(('-', 'No problems detected'))
		width = max(len('station'), max(len(i[0]) for i in notes))
		msg_width = max(len('note'), max(len(i[1]) for i in notes))
		rows = [f'{"station":<{width}} note', '-' * (1 + width + msg_width)]
		prev = notes[0][0] if notes else None
		for new, msg in notes:
			if new != prev:
				rows.append('')
			rows.append(f'{new:<{width}} {msg}')
			prev = new
		rows.extend([
			'',
			'* station  2-char station ID, baseline, closure set, '
				'or - for general notes',
			'* note     correlator notes and feedback'
		])
		sections['NOTES'] = '\n'.join(rows) + '\n'
		## CLOCK
		clocks = []
		for id, cc in vex_clocks.items():
			if id not in fringes.stations:
				continue
			for i, c in enumerate(sorted(cc)):
				u_offset = c.offset
				epoch = max(c.valid, master.datetime)
				if epoch != c.measured:
					u_offset += c.rate * (c.measured - epoch).total_seconds()
				if id in fits:
					r_offset, r_rate = fits[id].measured_offset, fits[id].rate
				else:
					r_offset = r_rate = NAN
				comment = ''
				s = stations[id]
				if i:
					comment = 'clock-break'
				elif ref in [id, stations[id].name, stations[id].mk4]:
					comment = 'reference'
				clocks.append([
					id, epoch, u_offset * 1e6, c.rate, r_offset * 1e6, r_rate,
					comment
				])
		sections['CLOCK'] = Table(data=dict(zip([
			'st', 'epoch', 'used-offset', 'used-rate', 'raw-offset', 'raw-rate',
			'comment'
		], zip(*clocks))), legend={
			'st': '2-char station ID',
			'epoch':
				'time coordinate of offsets and clock model segment start time',
			'used-offset':
				'(usec) station clock minus offset used in correlation '
				'at epoch',
			'used-rate':
				'drift rate of station clock minus offset used in correlation',
			'raw-offset':
				'(usec) station clock minus reference clock offset at epoch',
			'raw-rate':
				'drift rate of station clock minus reference clock offset',
			'comment': 'clock-break, reference station, or other notes'
		})
		## CHANNELS
		sections['CHANNELS'] = Table(data={
			'channel': [i.name for i in fringes.chans],
			'id': [i.mk4 for i in fringes.chans],
			'frequency': [i.freq / 1e6 for i in fringes.chans]
		}, legend={
			'channel': 'HOPS channel name',
			'id': 'short name with sideband indicator',
			'frequency': '(MHz) sky frequency'
		})
		## DROP_CHANNELS
		sections['DROP_CHANNELS'] = {k: list(v) for k, v in drop_chans.items()}
		## MANUAL_PCAL
		ids = sorted(id for id, man in fringes.man_pcals.items() if man)
		sections['MANUAL_PCAL'] = {id: () for id in ids}
		# QCODES
		all_codes = set(set(qcodes.values()) | set('0123456789')) - {'-', 'N'}
		all_codes = sorted(all_codes) + ['N', '-', 'total']
		codes = {}
		for baseline in baselines:
			for band in fringes.bands:
				codes[(baseline, band)] = {code: 0 for code in all_codes}
		for (_, _, bl, band), q in qcodes.items():
			codes[(bl, band)][q] += 1
		for cc in codes.values():
			cc['total'] = sum(cc.values())
		codes = {
			''.join(stations[id].mk4 for id in baselines[bl]) + ':' + band: v
			for (bl, band), v in codes.items()
		}
		codes['total'] = {
			code: sum(cc[code] for cc in codes.values()) for code in all_codes
		}
		table = {'bl:band': list(codes)}
		table.update(
			(code, [cc[code] for cc in codes.values()]) for code in all_codes
		)
		sections['QCODES'] = Table(data=table, legend={
			'bl:band': 'baseline and frequency band name',
			'0': 'no fringe detected',
			'1-9': 'fringe detected, higher value means better quality',
			'B': 'fourfit interpolation error',
			'D': 'no data in one or more frequency channels',
			'E': 'fringe found at edge of SBD, MBD, or rate window',
			'F': 'fork problem in processing',
			'G': 'channel amplitude diverges too far from mean amplitude',
			'H': 'low phase-cal amplitude in one or more channels',
			'N': 'correlation or fringing failed',
			'-': 'correlation not attempted',
			'total': 'column and row totals'
		})
		## SNR_RATIOS
		if snrr:
			snrr = sorted(snrr.items())
			snrr = {(s1, s2, band): snr for (s1, s2, band), snr in snrr}
			hh, vv = ['bl'], []
			for band in fringes.bands:
				hh.extend((band, f'n_{band}'))
			for s1, s2 in sorted(set((s1, s2) for s1, s2, _ in snrr)):
				v = [''.join(stations[id].mk4 for id in (s1, s2))]
				for band in fringes.bands:
					v.extend(snrr.get((s1, s2, band), (NAN, 0)))
				vv.append(v)
			table = dict(zip(hh, zip(*vv)))
			sections['SNR_RATIOS'] = Table(data=table, legend={
				'bl': 'baseline',
				'[A-Z]': 'ratio for this band name',
				'n_[A-Z]': 'number of scans in average for this band name'
			})
		## EOP
		if calc_files:
			legend = {
				'mjd': 'integer modified Julian date',
				'tai-utc': '(sec) TAI minus UTC offset',
				'ut1-utc': '(sec) UT1 minus UTC offset',
				'xpole': 'X pole EOP parameter',
				'ypole': 'Y pole EOP parameter'
			}
			table = dict(zip(legend, zip(*calcs.eop)))
			sections['EOP'] = Table(data=table, legend=legend)
		## CORRELATION
		sections['CORRELATION'] = v = {}
		v['SOFTWARE'] = ', '.join(sorted({
			SOFTWARE_NAME_MAP.get(i, i) for i in fringes.corr_sw
		}))
		if calc_files:
			v['VERSION'] = sorted(calcs.version)
		v['ALGORITHM'] = 'XF' if 'XF' in v['SOFTWARE'].upper() else 'FX'
		if input_files:
			v['NCHAN'] = inputs.nchan
			v['FFTSPECRES'] = [f'{x} MHz' for x in sorted(inputs.fftspecres)]
			v['SPECRES'] = [f'{x} MHz' for x in sorted(inputs.specres)]
			v['TINT'] = f'{inputs.tint} sec'
		if corr_sw:
			v['SOFTWARE'] = corr_sw
		if corr_version:
			v['VERSION'] = corr_version
		if corr_patch:
			v['PATCH'] = corr_patch
		## FRINGING
		sections['FRINGING'] = v = {'SOFTWARE': 'HOPS'}
		v['VERSION'] = fringes.ff_version
		if fringe_sw:
			v['SOFTWARE'] = fringe_sw
		if fringe_version:
			v['VERSION'] = fringe_version
		if fringe_patch:
			v['PATCH'] = fringe_patch
		## VGOSDB
		sections['VGOSDB'] = v = {'SOFTWARE': 'nuSolve'}
		if vgosdb_sw:
			v['SOFTWARE'] = vgosdb_sw
		if vgosdb_version:
			v['VERSION'] = vgosdb_version
		if vgosdb_patch:
			v['PATCH'] = vgosdb_patch
		## CORRELATION_CONFIG_FILE
		if paths := find_files(all_paths, re.compile(r'.*\.v2d$', re.I).match):
			v2ds = set()
			for path in paths:
				vlbi.info(f'reading {shlex.quote(path)}', verbose)
				text = read_path(path).replace('\t', '  ')
				for regex, sub, flags in [
					(r'\s*(#[^\n]*)?$', '', re.M),
					(r'(?<==|,)(\s*).*/', r'\1', 0),  # strip paths
					(r'^ *EOP\b.*?\{.*?\}\s*', '', re.I | re.M | re.S)
				]:
					text = re.sub(regex, sub, text, flags=flags)
				v2ds.add(text)
			if len(v2ds) > 1:
				v2ds = '\n\n'.join(
					f'# file {i}\n\n{file}\n' for i, file in enumerate(v2ds)
				)
			else:
				v2ds = next(iter(v2ds)) if v2ds else '# no configuration'
			sections['CORRELATION_CONFIG_FILE'] = v2ds.strip()
		else:
			vlbi.error('v2d file not found, skipping CORRELATION_CONFIG_FILE')
		## FRINGING_CONFIG_FILE
		cf = [str(vlbi.cf.CF(io.StringIO(text))) for text in fringes.cf]
		if not cf and (cf := find_files(all_paths, _IS_CF)):
			cf = sorted({str(vlbi.cf.CF(p)) for p in cf})
		if cf:
			if (n := len(fringes.cf)) > 1:
				vlbi.warn(f'found {n} CF files, including all of them')
				cf = '\n'.join(f'* file {i}\n\n{c}' for i, c in enumerate(cf))
			else:
				cf = next(iter(cf))
			sections['FRINGING_CONFIG_FILE'] = cf
		else:
			vlbi.warn('no fringing config (CF) file found')
		sections['END'] = ''
		return cls(sections)

	def __contains__(self, section: str) -> bool:
		return section.upper() in self._data

	def __getitem__(self, section: str) -> Union[str, dict, Table]:
		return self._data[section.upper()]

	def __setitem__(self, section: str, content: Union[str, dict, Table]):
		self._data[section.upper()] = content

	def __delitem__(self, section: str):
		del self._data[section.upper()]

	def __iter__(self) -> Iterator[str]:
		return iter(self._data)

	def __len__(self) -> int:
		return len(self._data)

	def setdefault(
		self, section: str, default: Union[str, dict, Table] = ''
	) -> Union[str, dict, Table]:
		section = section.upper()
		try:
			return self._data[section]
		except KeyError:
			self._data[section] = default
			return default

	def __repr__(self) -> str:
		return self.__class__.__name__ + '(' + repr(self._data) + ')'

	def __str__(self) -> str:
		results = ['%CORRELATOR_REPORT_FORMAT 3']
		for name, data in self._data.items():
			results.append(f'+{name}')
			if isinstance(data, str):
				results += [data] if data.strip() else []
			elif isinstance(data, Table):
				results.append(str(data))
			else:
				kv, width = [], max(map(len, data)) if data else 0
				for k, v in data.items():
					k = k + ' ' * (width - len(k))
					if v is None:
						kv.append(f'{k}\n')
					elif isinstance(v, (str, int, float)):
						kv.append(f'{k}  {v}\n')
					elif isinstance(v, datetime):
						if v.second or v.microsecond:
							kv.append(f'{k}  {v:%Y-%j-%H%M%S}\n')
						else:
							kv.append(f'{k}  {v:%Y-%j-%H%M}\n')
					else:
						vv = []
						for x in v:
							if isinstance(x, datetime):
								if x.second or x.microsecond:
									vv.append(f'{x:%Y-%j-%H%M%S}\n')
								else:
									vv.append(f'{x:%Y-%j-%H%M}\n')
							else:
								vv.append(str(x))
						kv.append(f'{k}  {", ".join(vv)}\n')
				results.append(''.join(kv))
		results = '\n\n'.join(i.strip('\n') for i in results if i) + '\n'
		return _RE_TRAILING_WS.sub('', results)

def main():
	'''run script'''
	env = os.environ.get
	A = argparse.ArgumentParser(description=__doc__)
	A.formatter_class = argparse.RawDescriptionHelpFormatter
	A.add_argument('path', nargs='*', default='.', help=(
		'VGOSDB tarball to extract report from, or list of files and/or '
		'directories in which to search recursively; should include Mk4 files, '
		'schedule, and station logs; first match of files or directory is used'
	))
	A.add_argument(
		'-o', '--out', metavar='PATH', default='',
		help="output file (default '' for stdout)"
	)
	A.add_argument(
		'-c', '--correlator', metavar='CORR', default=env('CORRNAME'),
		help='correlator name (default $CORRNAME or master file correlator)'
	)
	A.add_argument('-a', '--analyst', help=(
		'correlator analyst who processed session (default $CORRUSER '
		'or current user\'s long name with parentheticals removed)'
	))
	A.add_argument(
		'-V', '--version', metavar='CORR-FRNG', default='1-1',
		help='correlation and fringe release version respectively (default 1-1)'
	)
	A.add_argument(
		'-r', '--ref', metavar='ID', type=(lambda x: x.capitalize()),
		help='clock reference station 2-char ID'
	)
	A.add_argument(
		'-m', '--map', action='store_true', default=True,
		help='read nuSolve "cat-map" catalog map (default)'
	)
	A.add_argument(
		'-M', '--no-map', action='store_false', dest='map',
		help='don\'t read nuSolve "cat-map" catalog map'
	)
	A.add_argument(
		'--corr-sw', metavar='SW', default=env('CORRSW'),
		help='correlator software name (default $CORRSW or DiFX)'
	)
	A.add_argument(
		'--corr-version', metavar='VER', default=env('CORRVERSION'),
		help='correlator software version (default $CORRVERSION)'
	)
	A.add_argument(
		'--corr-patch', metavar='PATCH', default=env('CORRPATCH'),
		help='correlator software patch description (default $CORRPATCH)'
	)
	A.add_argument(
		'--fringe-sw', metavar='SW', default=env('FRINGESW'),
		help='fringing software name (default $FRINGESW or HOPS)'
	)
	A.add_argument(
		'--fringe-version', metavar='VER', default=env('FRINGEVERSION'),
		help='fringing software version (default $FRINGEVERSION)'
	)
	A.add_argument(
		'--fringe-patch', metavar='PATCH', default=env('FRINGEPATCH'),
		help='fringing software patch description (default $FRINGEPATCH)'
	)
	A.add_argument(
		'--vgosdb-sw', metavar='SW', default=env('VGOSDBSW'),
		help='VGOSDB software name (default $VGOSDBSW or nuSolve)'
	)
	A.add_argument(
		'--vgosdb-version', metavar='VER', default=env('VGOSDBVERSION'),
		help='VGOSDB software version (default $VGOSDBVERSION)'
	)
	A.add_argument(
		'--vgosdb-patch', metavar='PATCH', default=env('VGOSDBPATCH'),
		help='VGOSDB software patch description (default $VGOSDBPATCH)'
	)
	A.add_argument(
		'-v', '--verbose', action='store_true', help='show details on STDERR'
	)
	a = A.parse_args()
	# extract from VGOSDB
	if len(a.path) == 1 and a.path[0].lower().endswith(('.tgz', '.tar.gz')):
		try:
			text = read_report_text(a.path[0], False)[1]
		except IOError as e:
			vlbi.error(f'{e.strerror}: {e.filename}', exit=e.errno)
	else:
		# build from scratch
		text = str(Report.build(
			a.path, analyst=a.analyst, version=a.version, ref=a.ref,
			catmap=a.map, correlator=a.correlator,
			corr_sw=a.corr_sw, corr_version=a.corr_version,
			corr_patch=a.corr_patch,
			fringe_sw=a.fringe_sw, fringe_version=a.fringe_version,
			fringe_patch=a.fringe_patch,
			vgosdb_sw=a.vgosdb_sw, vgosdb_version=a.vgosdb_version,
			vgosdb_patch=a.vgosdb_patch,
			verbose=a.verbose
		))
	if a.out:
		with open(a.out, 'w') as f:
			f.write(text)
	else:
		sys.stdout.write(text)

if __name__ == '__main__':
	try:
		main()
	except (BrokenPipeError, KeyboardInterrupt):
		sys.stderr.write('\n')
		sys.stderr.close()
