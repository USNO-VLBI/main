#!/usr/bin/env python3

'''Read IVS ns-codes.txt, m.stations and stations.m files

usage:

* `ss = Stations()` read stations files from usual locations
* `ss['Kk']` get station with ID `Kk` (case-insensitive)
* `ss['KOKEE']` get station with name `KOKEE` (case-insensitive)
* `ss['K']` get station with HOPS Mk4 ID `K` (case-sensitive)
* `ss['Kk'].mk4id` convert regular station ID to HOPS Mk4 ID
'''

from typing import Iterable, Iterator, Union
import argparse
import collections.abc
import fnmatch
import os
import re
import shlex
import sys
import vlbi

FILENAMES = 'ns-codes.txt', 'stations.m', 'm.stations'

class Station(tuple):
	'''Details for a VLBI station'''

	def __new__(
		cls, id: str, name: str = None, domes: str = None,
		cdp: Union[int, str] = None, comment: str = None, mk4id: str = None
	):
		if cdp is not None:
			cdp = int(cdp) if isinstance(cdp, int) or cdp.strip('-') else None
		
		return tuple.__new__(cls, (
			id.capitalize(),
			name.upper().ljust(3, '-') if name and name.strip('-') else None,
			domes if domes and domes.strip('-') else None,
			cdp,
			comment.strip() or None if comment and comment.strip('-') else None,
			mk4id[0] if mk4id and mk4id.strip('-') else None
		))

	def updated(
		self, id: str = None, name: str = None, domes: str = None,
		cdp: int = None, comment: str = None, mk4id: str = None
	):
		'''Return a copied Station object with updated details'''
		return self.__class__(
			self.id if id is None else id,
			self.name if name is None else name,
			self.domes if domes is None else domes,
			self.cdp if cdp is None else cdp,
			self.comment if comment is None else comment,
			self.mk4id if mk4id is None else mk4id
		)

	@property
	def id(self) -> str:
		'''2-char station ID (unique, first capital, second lower)'''
		return self[0]

	@property
	def name(self) -> str:
		'''3-to-8-char IVS station name'''
		return self[1]

	@property
	def domes(self) -> str:
		'''DOMES identifier'''
		return self[2]

	@property
	def cdp(self) -> int:
		'''CDP locator number'''
		return self[3]

	@property
	def comment(self) -> str:
		'''comment about station'''
		return self[4]

	@property
	def mk4id(self) -> str:
		'''1-char HOPS Mk4 station ID (case-sensitive)'''
		return self[5]

	_fields = 'id', 'name', 'domes', 'cdp', 'comment', 'mk4id'

	def __repr__(self) -> str:
		return (self.__class__.__name__ + '(' + ', '.join(
			name + '=' + repr(value)
			for name, value in zip(self._fields, self) if value is not None
		) + ')')

	@property
	def m_stations(self) -> str:
		'''m.stations file lines'''
		if self.mk4id and self.id:
			return f'{self.mk4id} xx\n{self.mk4id} {self.id}\n'
		return ''

	@property
	def stations_m(self) -> str:
		'''stations.m file line'''
		return f'{self.id} {self.mk4id}\n' if self.mk4id and self.id else ''

	@property
	def ns_codes(self) -> str:
		'''ns-codes.txt file line'''
		if not (self.id, self.name or self.domes or self.cdp or self.comment):
			return ''
		id = self.id or '--'
		name = self.name or '--------'
		domes = self.domes or '---------'
		cdp = f'{self.cdp:04d}' if self.cdp else '----'
		return f' {id:<2} {name:<8} {domes:<9} {cdp} {self.comment or "-"}\n'

	@property
	def table(self) -> str:
		'''convert to table entry line'''

	def __str__(self) -> str:
		cdp = f'{self.cdp:04d}' if self.cdp else '----'
		text = f'{self.mk4id or "-":1} {self.id or "--":<2} '
		text += f'{self.name or "--------":<8} {self.domes or "---------":<9} '
		return text + f'{cdp} {self.comment or "-"}\n'

class Stations(collections.abc.MutableMapping):
	'''Dictionary mapping station ID, Mk4, and name to Station details

	* `source` may be a path, Station, or open file/list of lines
	* `source` defaults to `$CORRROOT/etc` or `.`

	usage:
	* `ss = Stations()` reads default stations files
	* `ss['Kk']` lookup by exact station ID
	* `ss['KOKEE']` lookup by first available station name
	* `ss['K']` lookup by first available station Mk4ID
	* `ss.get(station='KOKEE', multi=True)` lookup all name matches
	'''

	def __init__(
		self, *source: Union[str, Iterable[Union[Station, str]]],
		verbose: bool = False
	):
		self._data = {}
		self.read(*source, verbose=verbose)

	def read(
		self, *source: Union[str, Iterable[Union[Station, str]]],
		verbose: bool = False
	) -> 'Stations':
		'''Read data from source into this Stations object, return self

		* `source` may be a path, Station, or open file/list of lines
		* `source` defaults to `$CORRROOT/etc` or `.`
		'''
		# default sources
		if not source:
			source = [os.path.join(vlbi.ROOT, 'etc'), '.']
		# expand directories
		for src in source:
			if isinstance(src, Station):
				self.add(src)
			elif isinstance(src, str):
				try:
					for filename in os.listdir(src):
						if filename.lower() in FILENAMES:
							path = os.path.join(src, filename)
							vlbi.info(f'reading {shlex.quote(path)}', verbose)
							with open(path) as file:
								self.read_lines(file)
				except NotADirectoryError:
					vlbi.info(f'reading {shlex.quote(src)}', verbose)
					with open(src) as file:
						self.read_lines(file)
			else:
				self.read_lines(src)
		return self

	def read_lines(self, lines: Iterable[Union[Station, str]]) -> 'Stations':
		'''Read text lines and Stations into this object, return self'''
		for line in lines:
			if isinstance(line, Station):
				self.add(line)
			else:
				line = line.strip()
				if line[:1] not in '*':
					line = line.split(None, 4)
					if len(line) == 2:
						if len(line[0]) == 1:
							if len(line[1]) == 2 and line[1].lower() != 'xx':
								self.add(Station(line[1], mk4id=line[0]))
						elif len(line[0]) == 2 and len(line[1]) == 1:
							self.add(Station(line[0], mk4id=line[1]))
					elif len(line[0]) == 2:
						self.add(Station(*line))
		return self

	def add(self, station: Station):
		'''Add or update Station in this Stations object'''
		if station.id in self._data:
			self._data[station.id] = self._data[station.id].updated(*station)
		else:
			self._data[station.id] = station

	def __getitem__(self, key: str) -> Station:
		if len(key) == 2:
			return self._data[key.capitalize()]
		elif len(key) == 1:
			for s in self._data.values():
				if s.mk4id == key:
					return s
		else:
			name = key.upper()
			for s in self._data.values():
				if s.name == name:
					return s
		raise KeyError(key)

	def __setitem__(self, key: str, station: Station):
		if key not in (station.id, station.name, station.mk4id):
			raise ValueError('key does not match station', key, station)
		self.add(station)

	def __delitem__(self, key: str):
		if len(key) == 2:
			del self._data[key.capitalize()]
		else:
			found = False
			if len(key) == 1:
				for id, s in self._data.items():
					if key == s.mk4id:
						del self._data[id]
						found = True
			else:
				name = key.upper()
				for id, s in self._data.items():
					if name == s.name:
						del self._data[id]
						found = True
			if not found:
				raise KeyError(key)

	def __iter__(self) -> Iterator[str]:
		return iter(self._data)

	def __len__(self) -> int:
		return len(self._data)

	def __contains__(self, o: Union[str, Station]) -> bool:
		if isinstance(o, Station):
			return o.id in self._data
		if len(o) == 2:
			return o.capitalize() in self._data
		if len(o) == 1:
			return any(s.mk4id == o for s in self._data.values())
		name = o.upper()
		return any(s.name == name for s in self._data.values())

	def __repr__(self) -> str:
		if not self._data:
			return self.__class__.__name__ + '([])'
		text = ','.join('\n  ' + repr(s) for s in self._data.values())
		return self.__class__.__name__ + '([' + text + '\n])'

	def __str__(self) -> str:
		return ''.join(map(str, self._data.values()))

	@property
	def m_stations(self) -> str:
		'''m.stations file'''
		return ''.join(s.m_stations for s in self._data.values())

	@property
	def stations_m(self) -> str:
		'''stations.m file'''
		return ''.join(s.stations_m for s in self._data.values())

	@property
	def ns_codes(self) -> str:
		'''ns-codes.txt file'''
		return ''.join(s.ns_codes for s in self._data.values())

def main():
	'''Run as script.'''
	A = argparse.ArgumentParser(description=__doc__.partition('\n\n')[0])
	A.add_argument('path', nargs='*', help=(
		'path to stations.m/m.stations/ns-codes.txt file/directories'
		'(default searches $CORRPATH/etc and .)'
	))
	A.add_argument(
		'--id', action='append',
		help='filter by 2-char ID (case-insensitive glob)'
	)
	A.add_argument(
		'--name', action='append',
		help='filter by name (case-insensitive glob)'
	)
	A.add_argument(
		'--domes', action='append',
		help='filter by domes (case-insensitive glob)'
	)
	A.add_argument(
		'--cdp', action='append', type=int,
		help='filter by cdp (int)'
	)
	A.add_argument(
		'--comment', action='append',
		help='filter by comment (case-insensitive glob)'
	)
	A.add_argument(
		'--mk4id', action='append',
		help='filter by mk4id (case-sensitive)'
	)
	A.add_argument(
		'-t', '--table', action='store_const', const='t', default='t',
		help='output tabular format (default)'
	)
	A.add_argument(
		'-n', '--ns-codes', dest='table', action='store_const', const='n',
		help='output ns-codes.txt format'
	)
	A.add_argument(
		'-m', '--m-stations', dest='table', action='store_const', const='m',
		help='output m.stations format'
	)
	A.add_argument(
		'-s', '--stations-m', dest='table', action='store_const', const='s',
		help='output stations.m format'
	)
	A.add_argument(
		'-v', '--verbose', action='store_true', help='output details to STDERR'
	)
	a = A.parse_args()
	# read
	stations = Stations(*a.path, verbose=a.verbose)
	# filters
	for kind in ['id', 'name', 'domes', 'cdp', 'comment', 'mk4id']:
		if texts := getattr(a, kind):
			# compile pattern(s)
			patterns = []
			for text in texts:
				patterns.append(re.compile(fnmatch.translate(text), re.I))
			# apply filter
			new_stations = []
			for station in stations.values():
				value = getattr(station, kind)
				if value and any(pattern.match(value) for pattern in patterns):
					new_stations.append(station)
			stations = Stations(new_stations)
	# output
	if a.table == 't':
		h = 'Mk4ID\n| ID Name     DOMES     CDP  Comment'
		h = f'\033[2m{h}\033[22m\n' if sys.stderr.isatty() else h + '\n'
		sys.stderr.write(h)
		sys.stderr.flush()
		sys.stdout.write(str(stations))
	elif a.table == 'n':
		h = '*C- Name---- --DOMES-- CDP- Comments/description\n'
		h += '*cc nnnnnnnn ssssstmmm mmmm -----------------------------------'
		h = f'\033[2m{h}\033[22m\n' if sys.stderr.isatty() else h + '\n'
		sys.stderr.write(h)
		sys.stderr.flush()
		sys.stdout.write(stations.ns_codes)
	elif a.table == 's':
		sys.stdout.write(stations.stations_m)
	elif a.table == 'm':
		sys.stdout.write(stations.m_stations)

if __name__ == '__main__':
	try:
		main()
	except BrokenPipeError:
		sys.stderr.close()
	except KeyboardInterrupt:
		sys.stderr.write('\n')
