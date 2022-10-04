#!/usr/bin/env python3

'''Read nuSolve catalog map file

examples:

```
# read file
> m = CatMap('etc/cat-map')

# get station, source respectively
> m.stn['VLBA_ABC']  # == 'ABC-VLBA'
> m.src['ABC123']    # == '1234-567'
> m.src['NotInMap']  # == 'NotInMap'
```
'''

from typing import Iterable, Iterator, Mapping, Union
import collections.abc
import os
import re
import vlbi

DEFAULT_PATH = os.path.join(vlbi.ROOT, 'etc')

class CatMapDict(collections.abc.MutableMapping):
	'''Map of case-insensitive alias to real values'''

	def __init__(self, data: Mapping[str, str] = ()):
		if isinstance(data, Mapping):
			data = data.items()
		self._data = {k.lower(): (k, v) for k, v in data or ()}

	def __getitem__(self, key: str) -> str:
		try:
			return self._data[key.lower()][1]
		except KeyError:
			return key

	def __setitem__(self, key: str, value: str):
		self._data[key.lower()] = key, value

	def __delitem__(self, key: str):
		del self._data[key.lower()]

	def __iter__(self) -> Iterator[str]:
		return (key for key, _ in self._data.values())

	def __len__(self) -> int:
		return len(self._data)

	def __contains__(self, key: str) -> bool:
		return key.lower() in self._data

	def __repr__(self) -> str:
		v = ', '.join(repr(k) + ': ' + repr(v) for k, v in self._data.values())
		return self.__class__.__name__ + '({' + v + '})'

	def __str__(self) -> str:
		return ''.join(f'{k} => {v}\n' for k, v in self._data.values())

class CatMap:
	f'''Map of station (stn) and source (src) aliases to real values

	* Each `source` may be a file/dir path, open file or list of file lines
	* Default `source` is `{DEFAULT_PATH!r}`
	'''

	_re_comment = re.compile(r'#.*')
	_re_catmap = re.compile(r'(?:^|[._-])cat[._-]?map(?:[._-]|$)', re.I)

	def __init__(
		self, *source: Union[str, Iterable[str]], verbose: bool = False,
		stn: Mapping[str, str] = None, src: Mapping[str, str] = None
	):
		self.stn = CatMapDict(stn)
		self.src = CatMapDict(src)
		for f in source or [DEFAULT_PATH]:
			self.ingest(f, verbose)

	def ingest(self, source: Union[str, Iterable[str]], verbose=False):
		'''Read cat-map file into self

		* `source` is a file/dir path, open file or list of file lines
		'''
		if isinstance(source, str):
			if os.path.isdir(source):
				for file in sorted(os.listdir(source)):
					if self._re_catmap.search(file):
						path = os.path.join(source, file)
						vlbi.info(f'reading {path}', verbose)
						with open(path) as f:
							self.ingest(f)
			else:
				with open(source) as f:
					self.ingest(f)
			return
		for line in source:
			if line := self._re_comment.sub('', line).strip():
				t, _, rule = line.partition(':')
				k, _, v = rule.partition('=>')
				t, k, v = t.strip().lower(), k.strip(), v.strip()
				if k and v:
					if t == 'stn':
						self.stn[k] = v
					elif t == 'src':
						self.src[k] = v

	def __repr__(self) -> str:
		return f'{self.__class__.__name__}(stn={self.stn!r}, src={self.src!r})'

	def __str__(self) -> str:
		stn = src = ''
		if self.stn:
			stn = '\n# stations:\n'
			stn += ''.join(f'stn: {k} => {v}\n' for k, v in self.stn.items())
		if self.src:
			src = '\n# sources:\n'
			src += ''.join(f'src: {k} => {v}\n' for k, v in self.src.items())
		return ((stn + src).strip() or '# empty cat-map file') + '\n'
