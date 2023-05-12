#! /usr/bin/env python3

'''IVS master schedule file

usage:

* `s = get_session('r41061')` lookup a session (case-insensitive)
* `m = Master()` read and merge all master files
* `m = Master('master2021.txt')` read a single master file
* `s = m['r41061']` get session from a particular master (case-insensitive)
* `s.stations['Kk']` get whether station participated (case-insenstive)
'''

from typing import Callable, Iterable, Iterator, Mapping, Set, Union
import argparse
import collections.abc
import datetime as dt
import json
import math
import operator
import os
import re
import shlex
import stat
import sys
import vlbi

FORMAT_DATES = {
	'1.0': dt.datetime(2001, 8, 21), '2.0': dt.datetime(2022, 11, 1)
}
FORMAT_AUTHS = {'1.0': 'CCT&NRV', '2.0': 'CAD&CCT'}
MONS = 'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split()
MONTHS = (
	'January', 'February', 'March', 'April', 'May', 'June',
	'July', 'August', 'September', 'October', 'November', 'December'
)
RE_MAGIC = re.compile(r'''
	\#\#\s*master\sfile\sformat\sversion\s(\S+)  # format
	(?:\s+(\d{4})[^\d](\d\d)[^\d](\d\d))?(.*)  # format date, author
''', re.I | re.X)
RE_MASTER = re.compile(r'''
	(?:^|/)master(\d\d(\d\d)?)(?:-(.*)(?<!notes))?\.txt$
''', re.I | re.X)
RE_TITLE = re.compile(r'\bschedule\b', re.I)
RE_YEAR = re.compile(r'\b\d{4}\b')
RE_UPDATE = re.compile(rf'''
	\s*(?:last\s+)?updated
	(?:[\s-]*({'|'.join(MONTHS)})\s*(\d\d?)[\s,]+(\d\d\d\d))?
	[\s-]*([^\s-].*)?
''', re.I | re.X)
RE_PARTICIPATION = re.compile(r'\s*((?:\S\S)*)(?:\s*-\s*((?:\S\S)*))?\s*$')
SPLIT_STATIONS = re.compile(r'..').findall
_PROPERTY = lambda i, doc: property(operator.itemgetter(i), doc=doc)
_TYPES_CACHE: Mapping[str, Iterable[str]] = None

def NOOP(*_, **__):
	'''Do nothing'''

def listdir(path: str, strict: bool = False):
	'''Same as `os.listdir` but only raise `OSError` if `strict` is truthy'''
	try:
		return os.listdir(path)
	except OSError:
		if strict:
			raise
	return []

def get_session(
	code: str, *path: str,
	near: int = None,
	year: int = None,
	strict: bool = False,
	callback: Callable[[str], None] = NOOP
) -> 'Session':
	'''Return Session with code from disk

	* `code` is case insensitive
	* `near` is a year near the session to speed up searching
	* `path` defaults to `$CORRPATH/etc/*{master,control}`
	* `callback` is called with a file path when reading a file
	'''
	for path in Master.find(*path, near=near, year=year, strict=strict):
		(callback or NOOP)(path)
		try:
			return Master(path)[code]
		except OSError:
			if strict:
				raise
		except KeyError:
			continue
	raise KeyError(code)

def type_of(code: str) -> str:
	'''Type code for a session'''
	global _TYPES_CACHE
	if _TYPES_CACHE is None:
		with open(os.path.join(vlbi.ROOT, 'etc', 'master-type-map.json')) as f:
			_TYPES_CACHE = json.load(f)
		_TYPES_CACHE = {k: set(v) for k, v in _TYPES_CACHE.items()}
	code = code.lower()
	for type_name, codes in _TYPES_CACHE.items():
		if code in codes:
			return type_name
	return code.upper()

class Participation(collections.abc.MutableMapping):
	'''Maps `str` 2-char station ID to `bool` participation status'''

	def __init__(self, source: Union[str, Mapping[str, bool]] = None):
		self._data = {}
		if isinstance(source, str):
			if not (r := RE_PARTICIPATION.match(source)):
				raise ValueError(f'invalid stations: {source}')
			self._data.update((i, True) for i in SPLIT_STATIONS(r[1] or ''))
			self._data.update((i, False) for i in SPLIT_STATIONS(r[2] or ''))
		elif source:
			self.update(source)

	@property
	def participating(self) -> Set[str]:
		'''2-char station IDs for stations that are participating'''
		return frozenset(i for i, p in self._data.items() if p)

	@property
	def not_participating(self) -> Set[str]:
		'''2-char station IDs for stations that are not participating'''
		return frozenset(i for i, p in self._data.items() if not p)

	def __getitem__(self, station: str) -> bool:
		return self._data[station.capitalize()]

	def __setitem__(self, station: str, participating: bool):
		self._data[station.capitalize()] = bool(participating)

	def __delitem__(self, station: str):
		del self._data[station.capitalize()]

	def __iter__(self) -> Iterator[str]:
		return iter(self._data)

	def __len__(self) -> int:
		return len(self._data)

	def __repr__(self) -> str:
		return self.__class__.__name__ + '(' + repr(str(self)) + ')'

	def __str__(self) -> str:
		y = ''.join(i for i, p in self._data.items() if p)
		n = ''.join(i for i, p in self._data.items() if not p)
		return y + (' -' if y and n else '-' if n else '') + n

class Session(tuple):
	'''IVS master session schedule session'''

	_fields = (
		'code', 'type', 'date', 'time', 'duration', 'stations', 'scheduler',
		'correlator', 'analyzer', 'status', 'dbc', 'delay', 'name', 'pf', 'mk4'
	)

	__slots__ = ()

	def __new__(
		cls, code: str = None, type: str = None,
		date: dt.date = None, time: dt.time = None,
		duration: dt.timedelta = None, stations: Participation = None,
		scheduler: str = None, correlator: str = None, analyzer: str = None,
		status: Union[dt.date, str] = None, dbc: str = None,
		delay: float = None, name: str = None, pf: float = None, mk4: int = None
	):
		return tuple.__new__(cls, (
			code, type, date, time, duration, stations,
			scheduler, correlator, analyzer, status, dbc, delay, name, pf, mk4
		))

	code: str = _PROPERTY(0, 'session code (e.g. R4444)')

	@property
	def type(self) -> str:
		'''session type, version 2.0 only, (e.g. IVS-R4)'''
		if self[1] is None:
			return type_of(self[0])
		return self[1]

	date: dt.date = _PROPERTY(2, 'session start time')
	time: dt.time = _PROPERTY(3, 'session start time')

	@property
	def datetime(self) -> Union[dt.datetime, None]:
		'''session start datetime, or `None` if `time is None`'''
		return dt.datetime.combine(self[2], self[3]) if self[3] else None

	duration: dt.timedelta = _PROPERTY(4, 'approximate session length')
	stations: Participation = _PROPERTY(5, 'station participation')
	scheduler: str = _PROPERTY(6, 'IVS operation center')
	correlator: str = _PROPERTY(7, 'IVS correlation center')
	analyzer: str = _PROPERTY(8, 'IVS analysis center')
	status: Union[dt.date, str] = _PROPERTY(9, 'release date or status code')
	dbc: str = _PROPERTY(10, '2-char legacy X-band database code')
	delay: float = _PROPERTY(11, 'days from observation through analysis')

	@property
	def name(self) -> str:
		'''session name, version 1.0 only, (e.g. IVS-R4444)'''
		return self[0].upper() if self[12] is None else self[12]

	pf: float = _PROPERTY(13, '"processing factor", version 1.0 only')
	mk4: int = _PROPERTY(14, '4-digit Mk4 session number, version 1.0 only')

	@property
	def format_version(self) -> str:
		'''Highest native format version number for this record'''
		return '2.0' if self[1] is not None else '1.0'

	@property
	def db(self) -> str:
		'''database name'''
		return f'{self[2]:%Y%m%d}-{self[0].lower()}'

	@property
	def legacy_db(self) -> str:
		'''legacy database name (e.g. `21JAN01XX`)'''
		t = self[2]
		return f'{t:%y}{MONS[t.month - 1]}{t:%d}{self[10]:2}'

	def _asdict(self) -> Mapping:
		'''convert to new dict'''
		return dict(zip(self._fields, self))

	__dict__ = property(_asdict)

	def __getnewargs__(self) -> Iterable:
		'''used by copy and pickle'''
		return tuple(self)

	def __getstate__(self):
		'''exclude any dict from pickling'''

	def __repr__(self) -> str:
		return ('Session(' + ', '.join(
			f'{f}={v!r}' for f, v in zip(self._fields, self) if v is not None
		) + ')')

	def __str__(self) -> str:
		'''string representation (valid line for master schecule file)'''
		return '|%s|' % '|'.join(self.strings())

	def strings(self, format_version: str = None) -> Iterable[str]:
		'''Fields for `master*.txt`'''
		if (format_version or self.format_version) == '1.0':
			# status
			status = self.status or None
			if isinstance(self.status, dt.date):
				t = self.status
				status = f'{t:%y}{MONS[t.month - 1]}{t:%d}'
			# processing factor
			pf = ''
			if self.pf:
				pf = str(pf)
				if len(pf) < 3:
					pf = f'{float(self.pf):0.1f}'
			# duration
			duration = ''
			if self.duration:
				duration = str(math.ceil(self.duration.total_seconds() / 3600))
			# everything else
			return (
				self.name or '',
				(self.code or '').upper(),
				f'{MONS[self.date.month - 1]}{self.date:%d}',
				f'{self.date:%j}'.lstrip('0'),
				'' if self.time is None else f'{self.time:%H:%M}',
				duration,
				str(self.stations),
				self.scheduler or '',
				self.correlator or '',
				status or '',
				pf,
				self.dbc or '',
				self.analyzer or '',
				str('' if self.delay is None else self.delay),
				str('' if self.mk4 is None else self.mk4)
			)
		# status
		dt_status = isinstance(self.status, dt.date)
		duration = ''
		if self.duration:
			m = math.ceil(self.duration.total_seconds() / 60)
			duration = f'{m // 60:d}:{m % 60:02d}'
		# everything else
		return (
			self.type or '',
			f'{self.date:%Y%m%d}',
			self.code or '',
			f'{self.date:%j}'.lstrip('0'),
			'' if self.time is None else f'{self.time:%H:%M}',
			duration,
			str(self.stations),
			self.scheduler or '',
			self.correlator or '',
			f'{self.status:%Y%m%d}' if dt_status else self.status or '',
			self.dbc or '',
			self.analyzer or '',
			str('' if self.delay is None else self.delay)
		)

	@classmethod
	def from_line(cls, line: str, year: int = None) -> 'Session':
		'''Get session from a master file line'''
		line = line.split('|')
		h, _, m = line[5].partition(':')
		t = dt.time(int(h.strip() or 0), int(m.strip() or 0))
		# version 2.0
		if len(line) == 15:
			code = line[3].strip().lower()
			s = line[2].strip()
			d = dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
			if duration := line[6].strip() or None:
				dh, dm = line[6].split(':')
				duration = dt.timedelta(0, 3600 * int(dh) + 60 * float(dm))
			if st := line[10].strip() or None:
				if len(st) == 8:
					try:
						st = int(st[:4]), int(st[4:6]), int(st[6:8])
						st = dt.date(*st)
					except ValueError:
						pass
			delay_str = line[13].strip()
			return cls(
				code,                                   # code
				line[1].strip(),                        # type
				d, t,                                   # date, time
				duration,                               # duration
				Participation(line[7]),                 # stations
				line[8].strip() or None,                # scheduler
				line[9].strip() or None,                # correlator
				line[12].strip() or None,               # analyzer
				st,                                   # status
				line[11].strip() or None,               # dbc
				int(delay_str) if delay_str else None,  # delay
				None, None, None                        # name, pf, mk4
			)
		# version 1.0
		else:
			code = line[2].strip().lower()
			d = dt.date(year, 1, 1) + dt.timedelta(int(line[4]) - 1)
			if (st := line[10].strip() or None) and len(st) == 7:
				try:
					y = int(st[:2])
					y += 1900 if y >= 70 else 2000
					mo = MONS.index(st[2:5].upper()) + 1
					st = dt.date(y, mo, int(st[5:7]))
				except ValueError:
					pass
			pf_str = line[11].strip()
			delay_str = line[14].strip()
			mk4_str = line[15].strip()
			if duration := line[6].strip() or None:
				duration = dt.timedelta(hours=float(duration))
			return cls(
				code,                                   # code
				None,                                   # type
				d, t,                                   # date, time
				duration,                               # duration
				Participation(line[7]),                 # stations
				line[8].strip() or None,                # scheduler
				line[9].strip() or None,                # correlator
				line[13].strip() or None,               # analyzer
				st,                                   # status
				line[12].strip() or None,               # dbc
				int(delay_str) if delay_str else None,  # delay
				line[1].strip() or None,                # name
				float(pf_str) if pf_str else None,      # pf
				int(mk4_str) if mk4_str else None       # mk4
			)

class Master(collections.abc.MutableMapping):
	'''IVS master schedule file

	* `source` defaults to `$CORRPATH/etc/*{master,control}`
	* `source` treats `str` as file path but `Iterable[str]` as text lines
	* `year`: ignored for V2.0
	* `callback` is called with a file path when reading a file
	'''

	def __init__(
		self,
		*source: Union[str, Iterable[Union['Session', str]]],
		year: int = None,
		title: str = None,
		updated: dt.date = None,
		updater: str = None,
		format_version: str = None,
		format_date: dt.date = None,
		format_author: str = None,
		callback: Callable[[str], None] = NOOP
	):
		self._sessions = {}
		self.format_date = self.format_author = None
		self.format_version = format_version or '2.0'
		self.format_date = format_date or self.format_date
		self.format_author = format_author or self.format_author
		self.title = title
		self.updated, self.updater = updated, updater
		self.read(*source, year=year, callback=callback)

	@staticmethod
	def find(
		*path: str, near: int = None, year: int = None, strict: bool = False
	) -> Iterable[str]:
		'''Find master files

		* `path` defaults to `$CORRPATH/etc/*{master,control}`
		* `near` sorts by proximity to the `near` year
		'''
		# find paths in $CORRPATH/etc or ./etc
		if not path:
			etc = os.path.join(vlbi.ROOT, 'etc')
			path = [os.path.join(etc, f) for f in listdir(etc, strict) if (
				f.lower().endswith(('master', 'control')) or RE_MASTER.match(f)
			)]
		# search paths for master files
		ll = {}
		y0 = dt.datetime.utcnow().year if near is None else near
		for path in path:
			# directly-passed path
			if r := RE_MASTER.search(path):
				st = os.stat(path)
				if stat.S_ISREG(st.st_mode):
					y = int(r[1])
					if not r[2]:
						y += 1900 if y >= 70 else 2000
					if year and y != year:
						continue
					ll[(st.st_dev, st.st_ino)] = abs(y - y0), y > y0, y, path
			# directory
			for f in listdir(path, strict):
				if r := RE_MASTER.match(f):
					st = os.stat(p := os.path.join(path, f))
					if stat.S_ISREG(st.st_mode):
						y = int(r[1])
						if not r[2]:
							y += 1900 if y >= 70 else 2000
							if year and y != year:
								continue
						ll[(st.st_dev, st.st_ino)] = abs(y - y0), y > y0, y, p
			# sort by closeness to guessed year
			yield from (i[-1] for i in sorted(ll.values()))

	def read(
		self, *source: Union[str, Iterable[Union['Session', str]]],
		year: int = None, strict: bool = False,
		callback: Callable[[str], None] = NOOP
	) -> 'Master':
		'''Merge master file(s) from a source into existing Master object

		* `source` defaults to `$CORRPATH/etc/*{master,control}`
		* `source` treats `str` as file path, but `Iterable[str]` as lines
		* `year` ignored for V2.0
		* `callback` is called with a file path when reading a file
		'''
		for src in source or self.find(year=year, strict=strict):
			# Master or arbitrary mapping of {...: Session, ...}
			if isinstance(src, str):
				# file path
				try:
					with open(src) as file:
						(callback or NOOP)(src)
						self.read_lines(file, year=year, strict=strict)
				# directory path
				except IsADirectoryError:
					for p in self.find(src, year=year, strict=strict):
						try:
							with open(p) as file:
								(callback or NOOP)(src)
								self.read_lines(file, year=year, strict=strict)
						# file error
						except OSError:
							if strict:
								raise
				# directory error
				except OSError:
					if strict:
						raise
			# iterable
			else:
				self.read_lines(src, year=year, strict=strict)
		return self

	def read_lines(
		self, source: Iterable[Union[str, Session]], *,
		year: int = None, strict: bool = False
	) -> 'Master':
		'''Read lines from file or list of string lines'''
		for line in source:
			if isinstance(line, Session):
				self._sessions[line.code] = line
			if line.lstrip().startswith('#'):
				continue
			elif '|' in line:
				try:
					s = Session.from_line(line, year)
					self._sessions[s.code] = s
				except ValueError:
					if strict:
						raise
			elif r := RE_MAGIC.match(line):
				if r[1]:
					self.format_version = r[1]
				self.format_author = r[5].strip() or self.format_author
				d = dt.datetime(int(r[2]), int(r[3]), int(r[4]))
				if self.format_date:
					self.format_date = max(self.format_date, d)
				else:
					self.format_date = d
			elif RE_TITLE.search(line):
				if (title := line.strip()) != self.title:
					self.title = 'MERGED SCHEDULE' if self.title else title
				if r := RE_YEAR.search(line):
					year = int(r[0])
			elif r := RE_UPDATE.match(line):
				mo = MONTHS.index(r[1].capitalize()) + 1
				d = dt.datetime(int(r[3]), mo, int(r[2]))
				updater = ' '.join(r[4].strip().split())
				if not self.updated or self.updated < d:
					self.updated = d
					self.updater = updater
				elif self.updater and self.updater != updater:
					self.updater = 'various'
				else:
					self.updater = updater
		return self

	@property
	def format_version(self) -> str:
		'''Format version, `'1.0'` or `'2.0'`, also sets `format_version`'''
		if self._format_version:
			return self._format_version
		if self._sessions:
			return max(i.format_version for i in self._sessions.values())
		return '2.0'
	@format_version.setter
	def format_version(self, value):
		if not value:
			self.format_date = self._format_version = None
			return
		if not isinstance(value, str):
			f, s = f'{float(value):0.1f}', str(float(value))
			value = f if len(f) > len(s) else s
		self.format_date = FORMAT_DATES.get(value, self.format_date)
		self.format_author = FORMAT_AUTHS.get(value, self.format_author)
		self._format_version = value

	def __getitem__(self, code: str) -> Session:
		return self._sessions[code.lower()]

	def __setitem__(self, _, session: Session):
		if not isinstance(session, Session):
			raise TypeError('session must be a Session')
		self._sessions[session.code] = session

	def __delitem__(self, code: str):
		del self._sessions[code.lower()]

	def __iter__(self) -> Iterator[str]:
		return iter(self._sessions)

	def __len__(self) -> int:
		return len(self._sessions)

	def __repr__(self) -> str:
		kwargs = []
		for kwarg in [
			'format_version', 'format_date', 'format_author', 'title',
			'updated', 'updater'
		]:
			if getattr(self, kwarg) is not None:
				kwargs.append(', ' + kwarg + '=' + repr(getattr(self, kwarg)))
		return (self.__class__.__name__ + '([' + ','.join(
			'\n  ' + repr(i) for i in self._sessions.values()
		) + ('\n]' if self._sessions else ']') + ''.join(kwargs) + ')')

	def __str__(self) -> str:
		# magic number
		magic1 = f'## Master file format version {self.format_version}'
		magic2 = f'{self.format_date:%Y.%m.%d}' if self.format_date else ''
		magic2 += f' {self.format_author}' if self.format_author else ''
		magic2 = magic2.strip()
		space = ' ' * max(2, 80 - len(magic1) - len(magic2))
		magic = magic1 + space + magic2 + '\n'
		# title
		title = f'''{self.title or '':^80}'''.rstrip()
		title = f'\n{title}\n' if title else ''
		# last update
		update = ''
		if self.updated or self.updater:
			update = ['Last Updated', '-']
			if t := self.updated:
				update.append(f'{MONTHS[t.month - 1]} {t.day}, {t.year:04d}')
			if self.updater:
				update += ['-', self.updater]
			update = '\n' + f'''{' '.join(update):^80}'''.rstrip() + '\n'
		# format specifics
		format = self.format_version
		if not format:
			format = max(i.format_version for i in self._sessions)
		if format == '1.0':
			align, h1, h2, min_lens = zip(*[
				('<', 'SESSION', 'NAME', 10),
				('<', 'SESSION', 'CODE', 7),
				('<', 'DATE', 'mondd', 5),
				('>', 'DOY', 'ddd', 3),
				('<', 'TIME', 'hh:mm', 5),
				('>', 'DUR', 'hr', 3),
				('<', 'STATIONS', '', 8),
				('<', 'SKED', '', 4),
				('<', 'CORR', '', 4),
				('<', 'STATUS', 'yymondd', 7),
				('>', 'PF', '', 3),
				('^', 'DBC', 'CODE', 4),
				('<', 'SUBM', '', 4),
				('>', 'DEL', 'days', 4),
				('>', 'MK4', 'NUM', 4),
			])
		else:
			align, h1, h2, min_lens = zip(*[
				('<', 'SESSION', 'TYPE', 12),
				('<', 'DATE', 'yyyymmdd', 8),
				('<', 'SESSION', 'CODE', 12),
				('>', 'DOY', 'ddd', 3),
				('>', 'TIME', 'hh:mm', 5),
				('>', 'DUR', ' h:mm', 5),
				('<', 'STATIONS', '', 8),
				('<', 'SKED', '', 4),
				('<', 'CORR', '', 4),
				('<', 'STATUS', 'yyyymmdd', 8),
				('^', 'DBC', 'CODE', 4),
				('<', 'SUBM', '', 4),
				('>', 'DEL', 'days', 4),
			])
		# sessions
		key = lambda session: (
			session.date, session.time, session.type, session.code
		)
		data = []  # [(row_strings, month_tuple), ...]
		for i in sorted(self._sessions.values(), key=key):
			data.append((i.strings(format), (i.date.year, i.date.month)))
		# get max length for each column
		lens = [max(len(row[i]) for row, _ in data) for i in range(len(h1))]
		lens = [max(v) for v in zip(lens, min_lens)]
		sep = '-' * (sum(lens) + len(lens) + 1) + '\n'
		# build header
		head = '\n'
		for h in (h1, h2):
			h = ' '.join(f'{e:^{n}}' for e, n in zip(h, lens)).rstrip()
			head += ' ' + h + '\n'
		# build table rows
		rows = []
		last_month = None
		for row, month in data:
			# separator between months
			if month != last_month:
				last_month = month
				rows.append(sep)
			# normal row
			row = '|'.join(f'{i:{a}{n}}' for i, a, n in zip(row, align, lens))
			rows.append('|' + row + '|\n')
		# concatenate sections
		return magic + title + update + head + ''.join(rows) + sep + magic

def show_read_path(path: str):
	'''Show path for file being read'''
	start, stop = ('\033[2m', '\033[22m') if sys.stderr.isatty() else ('', '')
	sys.stderr.write(f'{start}reading {shlex.quote(path)}{stop}\n')

def main():
	'''Run script'''
	A = argparse.ArgumentParser()
	A.description = 'Read and output IVS master schedule file(s)'
	A.add_argument('source', nargs='*', help=(
		'master file path (default '' for STDIN if STDIN is a pipe, '
		'otherwise $CORRPATH/etc/*{master,control})'
	))
	A.add_argument(
		'-1', '--v1.0', dest='version', action='store_const', const='1.0',
		default=None, help='output in format version 1.0'
	)
	A.add_argument(
		'-2', '--v2.0', dest='version', action='store_const', const='2.0',
		help='output in format version 2.0'
	)
	A.add_argument('-s', '--session', help='session code to summarize')
	A.add_argument(
		'-v', '--verbose', action='store_true',
		help='show which master files are read'
	)
	a = A.parse_args()
	c = show_read_path if a.verbose else NOOP
	if a.source or sys.stdin.isatty():
		m = Master(*a.source, callback=c)
		if not a.version and m.format_version != '2.0' and (
			not a.source or len(a.source) != 1
		):
			m.format_version = '2.0'
	else:
		m = Master(sys.stdin, callback=c)
	if a.version:
		m.format_version = a.version
	elif len({s.date.year for s in m.values()}) > 1:
		m.format_version = '2.0'
	if a.session:
		regex = re.compile(a.session, re.I)
		m = Master([s for s in m.values() if regex.search(s.code)], callback=c)
	sys.stdout.write(str(m))

if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt:
		sys.stderr.write('\n')
	except BrokenPipeError:
		sys.stderr.flush()
		sys.stderr.close()
