#! /usr/bin/env python3

'''Read Mark4 (Mk4) format binary files, e.g. from fringing

usage:
* Get data from an Mk4 binary file with `mk4.records`
* Use `mk4.raw_records` to maintain order for editing
* Parse file names with `PATH`, e.g. `PATH('Kv.X.1.abcdef').type == 2`
* Convert dates to `datetime` with `date2datetime` and `dtype2datetime`
* Scrape details with `stat`, `get_pol`, and `get_ps`

compatibility:
* Supports type 1, 2, and 3 Mk4 files
* Targets HOPS 3.17, and may need updating for later versions
* Type 211 records are not supported
* Type 221 records without padding are untested
* Type 222 and 230 records are untested

credits:
* Module written by Phillip Haftings, Astronomer, Navy USNO EO VLBI
* HOPS Mk4 format developed by Haystack Observatory, MIT
  * Mk4 format spec: https://www.haystack.mit.edu/tech/vlbi/hops/mk4_files.txt
  * More complete documentation can be obtained by reading the HOPS source code
* LZRW3-A compression format developed by Ross Williams
'''

from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Callable, Iterable, Iterator, Mapping, Set, Tuple, Union
import argparse
import os
import re
import shlex
import shutil
import stat as _stat
import subprocess
import sys
import tempfile
import types
from numpy import dtype as _dtype, empty, frombuffer, ndarray, void

_FMT_PS_NAME = '{exp}.{time:%Y-%j-%H%M%S}.{bl}.{band}.{pol}.{src}.{root}.{run}'
_FMT_DATE = '%Y%j-%H%M%S'
_RANGE16 = tuple(range(16))
_RE_BCODE = re.compile(rb'[0-9A-Z]{6}$|[a-z{|}][a-z]{5}$')
_RE_SCODE = re.compile(r'[0-9A-Z]{6}$|[a-z{|}][a-z]{5}$')
_ROOT2INT = {c: i for i, c in enumerate('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}
_ROOT2INT_OLD = {c: i for i, c in enumerate('abcdefghijklmnopqrstuvwxyz{|}~')}
RE_PATH = re.compile(r'''
	(?:^.*/|^)(?:
		([a-zA-Z0-9$%]{2}\.)|  # type 1
		([a-zA-Z0-9$%]{2}\.[A-Z]\.[0-9]*)|  # type 2
		([a-zA-Z0-9$%]\.)|  # type 3
		([Ll][Oo][Gg])|  # type 4
		(\S*)  # type 0
	)\.([A-Z0-9]{6}|[a-z{][a-z]{5})$
''', re.X)

class PATH:
	'''Parsed Mk4 file path

	`type` is one of:
	* `0` schedule
	* `1` visibilities
	* `2` fringe
	* `3` station
	* `4` log
	* `None` unrecognized
	'''

	def __init__(
		self, path: str = '', *, type: int = None, stations: str = None,
		band: str = None, run: int = None, source: str = None, root: str = None
	):
		if path and (r := RE_PATH.match(path)):
			root = r[6] if root is None else root
			if m := r[1]:
				type = 1 if type is None else type
				stations = m[0:2] if stations is None else stations
			elif m := r[2]:
				type = 2 if type is None else type
				stations = m[0:2] if stations is None else stations
				band = m[3] if band is None else band
				run = int(m[5:] or 0) if run is None else run
			elif m := r[3]:
				type = 3 if type is None else type
				stations = m[0] if stations is None else stations
			elif m := r[5]:
				type, source = 0, m
			elif r[4]:
				type = 4
		self.type, self.stations, self.band = type, stations, band
		self.run, self.source, self.root = run, source, root

	def __repr__(self):
		return (f'{self.__class__.__name__}(' + ', '.join(
			f'{k}={v!r}' for k, v in [
				('type', self.type), ('stations', self.stations),
				('band', self.band), ('run', self.run),
				('source', self.source), ('root', self.root)
			 ] if v is not None
		) + ')')

dtype_sky_coord = _dtype([
	('ra_hr', '>i2'), ('ra_min', '>i2'), ('ra_sec', '>f4'),
	('dec_deg', '>i2'), ('dec_min', '>i2'), ('dec_sec', '>f4'),
])
dtype_cells = _dtype([('r', '>i4', 33), ('l', '>i4', 33)])
dtype_chan = _dtype([
	('index', '>i2'), ('sample_rate', '>u2'), ('sideband', 'c', 2),
	('polarization', 'c', 2), ('freq', '>f8', 2), ('chan_id', 'S8', 2)
])
dtype_date = _dtype([
	('y', '>i2'), ('d', '>i2'), ('H', '>i2'), ('M', '>i2'), ('S', '>f4')
])
dtype_ffit_chan = _dtype([('id', 'c'), ('unused', 'S1'), ('chans', '>i2', 4)])
dtype_header = _dtype([('id', 'S3'), ('ver', 'S2'), ('fmt', 'B'), ('n', '>i2')])
dtype_phasor_v0 = _dtype([('amp', '>f4'), ('phase', '>f4')])
dtype_phasor_v1 = _dtype([('amp', '>f4'), ('phase', '>f4'), ('weight', '>f4')])
dtype_polars = _dtype([('amp', '>f4'), ('phase', '>f4')])
dtype_sbandf = _dtype([('lsb', '>f4'), ('usb', '>f4')])
dtype_sbweights = _dtype([('lsb', '>f8'), ('usb', '>f8')])
dtype_sidebands = _dtype([('lsb', '>i2'), ('usb', '>i2')])

dtype_record_000 = _dtype([
	('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'S3'),
	('date', 'S16'), ('name', 'S40')
])

def dtype_record_101(f, skip=False):
	'''dtype generator for record type 101'''
	d = f.read(8)
	n = d[6] * 256 + d[7]
	l = 40 + 4 * n
	f.seek(l - 8 if skip else -8, 1)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'),
		('status', 'c'), ('n_blocks', '>i2'), ('index', '>i2'),
		('primary', '>i2'), ('chan_id', 'S8', 2),
		('corr_board', '>i2'), ('corr_slot', '>i2'), ('chan', '>i2', 2),
		('post_mortem', '>i4'), ('blocks', '>i4', (n,))
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 101')
	return result[0]

def dtype_record_110(f, skip=False):
	'''dtype generator for record type 110'''
	d = f.read(8)
	n = d[6] * 256 + d[7]
	l = 44 + 264 * n
	f.seek(l - 8 if skip else -8, 1)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'c'),
		('n_blocks', '>i2'), ('unused2', 'S2'), ('baseline', 'S2'),
		('file_num', '>i2'), ('root_code', 'S6'), ('index', '>i4'),
		('ap', '>i4'), ('flag', '>i4'), ('status', '>i4'),
		('bitshift', '>f4'), ('frac_bitshift', '>f4'),
		('data', dtype_cells, (n,))
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 110')
	return result[0]

def dtype_record_120(f, skip=False):
	'''dtype generator for record type 120'''
	d = f.read(8)
	n, t, l = d[6] * 256 + d[7], d[5], 40
	dt = [
		('record_id', 'S3'), ('record_ver', 'S2'), ('type', 'c'),
		('n_lags', '>i2'), ('baseline', 'S2'), ('root_code', 'S6'),
		('index', '>i4'), ('ap', '>i4'), ('flag', '>i4'), ('status', '>i4'),
		('fr_delay', '>i4'), ('delay_rate', '>i4')
	]
	# counts_per_lag
	if t == 1:
		l += 16 * n
		if skip:
			f.seek(l - 8, 1)
			return
		dt.append(('counts_per_lag', [
			('cos_cor', '>i4'), ('cos_bits', '>i4'),
			('sin_cor', '>i4'), ('sin_bits', '>i4')
		], (n,)))
	# counts_global
	elif t == 2:
		l += 8 + 8 * n
		if skip:
			f.seek(l - 8, 1)
			return
		dt.extend([
			('cos_bits', '>i4'), ('sin_bits', '>i4'),
			('lag_tags', [('cos_cor', '>i4'), ('sin_cor', '>i4')], (n,))
		])
	# auto_global
	elif t == 3:
		l += 8 + 4 * n
		if skip:
			f.seek(l - 8, 1)
			return
		dt.extend([
			('cos_bits', '>i4'), ('unused', 'S4'), ('cos_cor', '>i4', (n,))
		])
	# auto_per_lag
	elif t == 4:
		l += 8 * n
		if skip:
			f.seek(l - 8, 1)
			return
		dt.append(('auto_per_lag', [
			('cos_cor', '>i4'), ('cos_bits', '>i4')
		], (n,)))
	# spectral
	elif t == 5:
		l += 8 * n
		if skip:
			f.seek(l - 8, 1)
			return
		dt.append(('spectral', 'c8', (n,)))
		dt[8] = ('weight', '>f4')
	else:
		raise ValueError('mk4: Unknown record 120 type: %d' % t)
	f.seek(-8, 1)
	result = empty(1, dt)
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 120')
	return result[0]

def dtype_record_212(f, skip=False):
	'''dtype generator for record type 212'''
	d = f.read(8)
	n = d[6] * 256 + d[7]
	n += n % 2  # padding
	phasor = (dtype_phasor_v0, dtype_phasor_v1)[d[3:5] != b'00']
	l = 16 + phasor.itemsize * n
	f.seek(l - 8 if skip else -8, 1)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'c'),
		('n_ap', '>i2'), ('first_ap', '>i2'), ('channel', '>i2'),
		('sbd_chan', '>i2'), ('unused2', 'S2'), ('data', phasor, (n,))
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 212')
	return result[0]

def dtype_record_220(f, skip=False):
	'''dtype generator for record type 220'''
	d = f.read(12)
	n = (d[8] * 256 + d[9]) * (d[10] * 256 + d[11])
	l = 12 + n
	f.seek(l - 12 if skip else -12, 1)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'S3'),
		('width', '>2i'), ('height', '>2i'), ('fplot', 'c', (n,))
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 220')
	return result[0]

def dtype_record_221(f, skip=False):
	'''dtype generator for record type 221'''
	d = f.read(12)
	n = d[11] + 256 * (d[10] + 256 * (d[9] + 256 * d[8]))
	# apply padding
	# this formula seems to work, but makes no sense
	if d[6:8] != b'\x00\x00':
		n += 10 - (n - 2) % 8
	else:
		sys.stderr.write('mk4: untested: type 221 record without padding\n')
	if n < 1:
		n = 1
	l = 12 + n
	f.seek(l - 12 if skip else -12, 1)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'c'),
		('padded', '>i2'), ('ps_len', '>i4'), ('ps_plot', 'c', (n,))
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 221')
	return result[0]

def dtype_record_222(f, skip=False):
	'''dtype generator for record type 222'''

	setstring_length, cf_length = frombuffer(f.read(24), '>i4', 6)[4:6]
	setstring_length = ((setstring_length + 7) & ~7) + 8
	cf_length = ((cf_length + 7) & ~7) + 8
	n = setstring_length + cf_length + 8
	f.seek(n if skip else -24, 1)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'c'),
		('padded', '>i2'), ('setstring_hash', '>i4'), ('control_hash', '>i4'),
		('setstring_length', '>i4'), ('cf_length', '>i4'),
		('setstring', 'S%d' % setstring_length),
		('cf', 'S%d' % cf_length), ('padding', 'S8')
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 222')
	return result[0]

def dtype_record_230(f, skip=False):
	'''dtype generator for record type 230'''
	sys.stderr.write('mk4: untested: type 230 record\n')
	d = f.read(8)
	n = d[6] * 256 + d[7]
	l = 24 + 16 * n
	f.seek(l - 8 if skip else -8)
	if skip:
		return
	result = empty(1, [
		('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'c'),
		('n_spec_pts', '>i2'), ('frq', '>i4'), ('ap', '>i4'),
		('lsb_weight', '>f4'), ('usb_weight', '>f4'), ('xpower', '>c16', (n,))
	])
	if f.readinto(result) != result.itemsize:
		raise EOFError('end of file reached while reading record 230')
	return result[0]

_rec_start = [('record_id', 'S3'), ('record_ver', 'S2'), ('unused', 'S3')]
_rec_ap_start = _rec_start + [
	('baseline', 'S2'), ('root_code', 'S6'), ('index', '>u4'), ('ap', '>u4')
]
# dtype_record  {(record, subtype): (dtype|func(file)=>rec), ...}
dtype_record: Mapping[Tuple[int, int], Union[_dtype, Callable[[str], void]]] = {
	(0, 0): dtype_record_000,
	(0, 1): dtype_record_000,
	(100, 0): _dtype(_rec_start + [
		('proc_time', dtype_date), ('baseline', 'S2'), ('rootname', 'S34'),
		('qcode', 'S2'), ('unused2', 'S6'), ('pct_done', '>f4'),
		('start', dtype_date), ('stop', dtype_date), ('n_drec', '>i4'),
		('n_index', '>i4'), ('n_lags', '>i2'), ('n_blocks', '>i2')
	]),
	(101, 0): dtype_record_101,
	(110, 0): dtype_record_110,
	(120, 0): dtype_record_120,
	(130, 0): _dtype(_rec_ap_start + [
		('enabled', '>u4'), ('occurred', '>u4'),
		('x_su_id', '>i4'), ('x_ch_id', '>i4'), ('x_cf_num', '>i4'),
		('x_checksum', '>i4'), ('x_in_bd_link', '>i4'), ('x_in_bd_sync', '>i4'),
		('y_su_id', '>i4'), ('y_ch_id', '>i4'), ('y_cf_num', '>i4'),
		('y_checksum', '>i4'), ('y_in_bd_link', '>i4'), ('y_in_bd_sync', '>i4'),
		('head_tape_past_end', '>i4'), ('head_tape_3_carry', '>i4'),
		('tail_tape_past_end', '>i4'), ('tail_tape_3_carry', '>i4')
	]),
	(131, 0): _dtype(_rec_ap_start + [('link_status', '>u4', 64)]),
	(141, 0): _dtype(_rec_ap_start + [
		('su_id', '>u4'), ('ch_id', '>u4'), ('cf_num', '>u4'),
		('delay_err', '>i4'), ('delay_err_rate', '>i4'),
		('phase', '>i4'), ('phase_rate', '>i4'), ('phase_acc', '>i4'),
		('phase_log_inc_period', '>i4'), ('phase_k_acc_seg_len', '>i4'),
		('sideband', '>i4'), ('oversampling_factor', '>i4'),
		('checksum', '>u4'), ('flags', '>u4')
	]),
	(142, 0): _dtype(_rec_ap_start + [
		('phase_adj', '>u4'),
		('phase_inc_clk_div', '>u4'), ('phase_rate_inc_cnt', '>u4'),
		('phase', '>i4'), ('phase_rate', '>i4'), ('phase_acc', '>i4'),
		('x_delay', '>i4'), ('x_delay_rate', '>i4'),
		('y_delay', '>i4'), ('y_delay_rate', '>i4'),
		('b_delay', '>i4'), ('b_delay_rate', '>i4'), ('tape_pos', '>u4'),
		('x_dly_rate_sign', '>u4'), ('y_dly_rate_sign', '>u4'),
		('b_dly_rate_sign', '>u4'), ('udr', '>u4'), ('unused2', 'S4')
	]),
	(143, 0): _dtype(_rec_ap_start + [
		('phase_adj', '>u4'),
		('phase_inc_clk_div', '>u4'), ('phase_rate_inc_cnt_final', '>u4'),
		('phase_rate_final', '>i4'), ('phase_final', '>i4'),
		('phase_initial', '>i4'),
		('x_delay_final', '>i4'), ('x_delay_initial', '>i4'),
		('y_delay_final', '>i4'), ('y_delay_initial', '>i4'),
		('b_delay_final', '>i4'), ('b_delay_initial', '>i4'),
		('tape_pos_final', '>u4'), ('tape_err', '>u4'), ('udr', '>u4'),
		('unused2', 'S4')
	]),
	(144, 0): _dtype(_rec_ap_start + [
		('su_id_ex', '>u4'), ('su_id_rx', '>u4'),
		('ch_id_ex', '>u4'), ('ch_id_rx', '>u4'),
		('cf_num_ex', '>u4'), ('cf_num_rx', '>u4'),
		('checksum_ex', '>u4'), ('checksum_rx', '>u4')
	]),
	(150, 0): _dtype(_rec_ap_start + [('qcode', 'S2'), ('unused2', 'S6')]),
	(200, 0): _dtype(_rec_start + [
		('software_revision', '>i2', 10),
		('experiment_number', '>i4'), ('experiment_name', 'S32'),
		('scan_name', 'S32'), ('corr_name', 'S8'), ('scan_time', dtype_date),
		('start_offset', '>i4'), ('stop_offset', '>i4'),
		('corr_time', dtype_date), ('ffit_proc_time', dtype_date),
		('ffit_ref_time', dtype_date)
	]),
	(201, 0): _dtype(_rec_start + [
		('source', 'S32'), ('coord', dtype_sky_coord), ('epoch', '>i2'),
		('unused2', 'S2'), ('coord_time', dtype_date),
		('ra_rate', '>f8'), ('dec_rate', '>f8'),
		('pulsar_phase', '>f8', 4), ('pulsar_epoch', '>f8'),
		('dispersion', '>f8')
	]),
	(202, 0): _dtype(_rec_start + [
		('baseline', 'S2'), ('station_id', 'S2', 2), ('station_name', 'S8', 2),
		('tape', 'S8', 2), ('nlags', '>i2'), ('position', '>f8', (3, 2)),
		('u', '>f8'), ('v', '>f8'), ('uf', '>f8'), ('vf', '>f8'),
		('clock', '>f4', 2), ('clock_rate', '>f4', 2),
		('instrument_delay', '>f4', 2), ('z_atm_delay', '>f4', 2),
		('elevation', '>f4', 2), ('azimuth', '>f4', 2)
	]),
	(203, 0): _dtype(_rec_start + [('channels', dtype_chan, 32)]),
	(203, 1): _dtype(_rec_start + [('channels', dtype_chan, 512)]),
	(204, 0): _dtype(_rec_start + [
		('ff_version', '>i2', 2), ('platform', 'S8'), ('control_file', 'S96'),
		('cf_time', dtype_date), ('override', 'S128')
	]),
	(205, 0): _dtype(_rec_start + [
		('utc_central', dtype_date),
		('offset', '>f4'),  # Offset of FRT from scan ctr sec
		('ffmode', 'c', 8),  # Fourfit execution modes
		('search', '>f4', (3, 2)),  # SBD, MBD, rate search win (us, us, us/s)
		('filter', '>f4', 8),  # Various filter thresholds
		('start', dtype_date),  # Start of requested data span
		('stop', dtype_date),  # End of requested data span
		('ref_freq', '>f8'),  # Fourfit reference frequency Hz
		('ffit_chans', dtype_ffit_chan, 16)  # Fourfit channel id info
	]),
	(205, 1): _dtype(_rec_start + [
		('utc_central', dtype_date),
		('offset', '>f4'),  # Offset of FRT from scan ctr sec
		('ffmode', 'c', 8),  # Fourfit execution modes
		('search', '>f4', (3, 2)),  # SBD, MBD, rate search win (us, us, us/s)
		('filter', '>f4', 8),  # Various filter thresholds
		('start', dtype_date),  # Start of requested data span
		('stop', dtype_date),  # End of requested data span
		('ref_freq', '>f8'),  # Fourfit reference frequency Hz
		('ffit_chans', dtype_ffit_chan, 64)  # Fourfit channel id info
	]),
	(206, 0): _dtype(_rec_start + [
		('start', dtype_date), ('first_ap', '>i2'), ('last_ap', '>i2'),
		('accepted', dtype_sidebands, 16),
		('integration_time', '>f4'), ('accept_ratio', '>f4'),
		('discard', '>f4'), ('reasons', dtype_sidebands, (8, 16)),
		('rate_size', '>i2'), ('mbd_size', '>i2'), ('sbd_size', '>i2'),
		('unused2', 'S6')
	]),
	(206, 1): _dtype(_rec_start + [
		('start', dtype_date), ('first_ap', '>i2'), ('last_ap', '>i2'),
		('accepted', dtype_sidebands, 16), ('weights', dtype_sbweights, 16),
		('integration_time', '>f4'), ('accept_ratio', '>f4'),
		('discard', '>f4'), ('reasons', dtype_sidebands, (8, 16)),
		('rate_size', '>i2'), ('mbd_size', '>i2'), ('sbd_size', '>i2'),
		('unused2', 'S6')
	]),
	(206, 2): _dtype(_rec_start + [
		('start', dtype_date), ('first_ap', '>i2'), ('last_ap', '>i2'),
		('accepted', dtype_sidebands, 64),('weights', dtype_sbweights, 64),
		('integration_time', '>f4'), ('accept_ratio', '>f4'),
		('discard', '>f4'), ('reasons', dtype_sidebands, (8, 64)),
		('rate_size', '>i2'), ('mbd_size', '>i2'), ('sbd_size', '>i2'),
		('unused2', 'S6')
	]),
	(207, 0): _dtype(_rec_start + [
		('pc_amp', dtype_sbandf, (2, 16)), ('pc_phase', dtype_sbandf, (2, 16)),
		('pc_freq', dtype_sbandf, (2, 16)), ('pc_rate', '>f4', 2),
		('err_rate', '>f4', (2, 16))
	]),
	(207, 1): _dtype(_rec_start + [
		('pc_mode', '>i4'), ('unused2', '>i4'),
		('pc_amp', dtype_sbandf, (2, 16)), ('pc_phase', dtype_sbandf, (2, 16)),
		('pc_offset', dtype_sbandf, (2, 16)),
		('pc_freq', dtype_sbandf, (2, 16)), ('pc_rate', '>f4', 2),
		('err_rate', '>f4', (2, 16))
	]),
	(207, 2): _dtype(_rec_start + [
		('pc_mode', '>i4'), ('unused2', '>i4'),
		('pc_amp', dtype_sbandf, (2, 64)), ('pc_phase', dtype_sbandf, (2, 64)),
		('pc_offset', dtype_sbandf, (2, 64)),
		('pc_freq', dtype_sbandf, (2, 64)), ('pc_rate', '>f4', 2),
		('err_rate', '>f4', (2, 64))
	]),
	(208, 0): _dtype(_rec_start + [
		('quality', 'c'), ('errcode', 'c'), ('tape_qcode', 'c', 6),
		('apriori_delay', '>f8'), ('apriori_rate', '>f8'),
		('apriori_accel', '>f8'), ('total_mbd', '>f8'), ('total_sbd', '>f8'),
		('total_rate', '>f8'), ('total_mbd_ref', '>f8'),
		('total_sbd_ref', '>f8'), ('total_rate_ref', '>f8'),
		('resid_mbd', '>f4'), ('resid_sbd', '>f4'), ('resid_rate', '>f4'),
		('mbd_err', '>f4'), ('sbd_err', '>f4'), ('rate_err', '>f4'),
		('ambiguity', '>f4'), ('amplitude', '>f4'), ('inc_seg_amp', '>f4'),
		('inc_chan_amp', '>f4'), ('snr', '>f4'), ('prob_false', '>f4'),
		('total_phase', '>f4'), ('resid_phase', '>f4')
	]),
	(208, 1): _dtype(_rec_start + [
		('quality', 'c'), ('errcode', 'c'), ('tape_qcode', 'c', 6),
		('apriori_delay', '>f8'), ('apriori_rate', '>f8'),
		('apriori_accel', '>f8'), ('total_mbd', '>f8'), ('total_sbd', '>f8'),
		('total_rate', '>f8'), ('total_mbd_ref', '>f8'),
		('total_sbd_ref', '>f8'), ('total_rate_ref', '>f8'),
		('resid_mbd', '>f4'), ('resid_sbd', '>f4'), ('resid_rate', '>f4'),
		('mbd_err', '>f4'), ('sbd_err', '>f4'), ('rate_err', '>f4'),
		('ambiguity', '>f4'), ('amplitude', '>f4'), ('inc_seg_amp', '>f4'),
		('inc_chan_amp', '>f4'), ('snr', '>f4'), ('prob_false', '>f4'),
		('total_phase', '>f4'), ('total_phase_ref', '>f4'),
		('resid_phase', '>f4'), ('tec_err', '>f4')
	]),
	(210, 0): _dtype(_rec_start + [('amp_phase', dtype_polars, 16)]),
	(210, 1): _dtype(_rec_start + [('amp_phase', dtype_polars, 64)]),
	(212, 0): dtype_record_212,
	(212, 1): dtype_record_212,
	(220, 0): dtype_record_220,
	(221, 0): dtype_record_221,
	(222, 0): dtype_record_222,
	(230, 0): dtype_record_230,
	(300, 0): _dtype(_rec_start + [
		('id', 'c'), ('station_id', 'S2'), ('name', 'S32'), ('unused2', 'c'),
		('model_start', dtype_date), ('model_interval', '>f4'),
		('n_splines', '>i2'), ('unused3', 'S2')
		# TODO Why isn't `unused3` in the C struct?
	]),
	(301, 0): _dtype(_rec_start + [
		('interval', '>i2'), ('chan_id', 'S32'), ('unused2', 'S6'),
		('delay_spline', '>f8', 6)
	]),
	(302, 0): _dtype(_rec_start + [
		('interval', '>i2'), ('chan_id', 'S32'), ('unused2', 'S6'),
		('phase_spline', '>f8', 6)
	]),
	(303, 0): _dtype(_rec_start + [
		('interval', '>i2'), ('chan_id', 'S32'), ('unused2', 'S6'),
		('azimuth', '>f8', 6), ('elevation', '>f8', 6),
		('parallactic_angle', '>f8', 6),
		('u', '>f8', 6), ('v', '>f8', 6), ('w', '>f8', 6)
	]),
	(304, 0): _dtype(_rec_start + [
		('time', dtype_date), ('duration', '>f4'),
		('track_stats', [
			('error_count', '>i4'),
			('frames', '>i4'),
			('bad_frames', '>i4'),
			('slip_sync', '>i4'),
			('missing_sync', '>i4'),
			('crc_error', '>i4')
		], 64)
	]),
	(306, 0): _dtype(_rec_start + [
		('time', dtype_date), ('duration', '>f4'),
		('state_counts', [
			('chan_id', 'S32'), ('big_pos', '>i4'), ('pos', '>i4'),
			('neg', '>i4'), ('big_neg', '>i4')
		], 16)
	]),
	(307, 0): _dtype(_rec_start + [
		('su', '>i4'), ('unused0', 'S4'), ('tot', '>f8'), ('rot', '>f8'),
		('accum_period', '>f8'), ('frame_count', '>u4'),
		('counts', [('count', '>u4', 8), ('val_count', '>u4')], 16),
		('unused1', 'S4')
	]),
	(308, 0): _dtype(_rec_start + [
		('time', dtype_date), ('duration', '>f4'),
		('pcal', [
			('chan_id', 'S8'), ('frequency', '>f4'),
			('real', '>f4'), ('imaginary', '>f4')
		], 32)
	]),
	(309, 0): _dtype(_rec_start + [
		('su', '>i4'), ('ntones', '>i4'), ('rot', '>f8'), ('acc_period', '>f8'),
		('ch_tag', [
			('chan_name', 'S8'), ('freq', '>f8'), ('acc', '>i4', (16, 2))
		], 16)
	]),
	(309, 1): _dtype(_rec_start + [
		('su', '>i4'), ('ntones', '>i4'), ('rot', '>f8'), ('acc_period', '>f8'),
		('ch_tag', [
			('chan_name', 'S8'), ('freq', '>f8'), ('acc', '>i4', (64, 2))
		], 64)
	]),
	None: None
}

def BAND(text: str) -> Set[str]:
	'''Convert text to freq band'''
	text = text.strip()
	return {None} if not text or '*' in text or '?' in text else set(text)

def BASELINE(text: str) -> Tuple[Set[str], str, bool]:
	'''Convert text to baseline, freq band, and auto-correlation flag'''
	bl, _, f = text.partition(':')
	if len(bl) <= 2:
		bl = [s for s in bl if s not in '*?']
	else:
		bl = [s for s in bl.split('-') if s not in ('', '*', '?')]
	bl_set = set(bl)
	return bl_set, BAND(f), len(bl) > len(bl_set)

def root2int(code: str) -> str:
	'''Convert HOPS Mk4 root code to (usually 4 sec) units since epoch'''
	return ((
		_ROOT2INT[code[5]] + _ROOT2INT[code[4]] * 36 +
		_ROOT2INT[code[3]] * 1296 + _ROOT2INT[code[2]] * 46656 +
		_ROOT2INT[code[1]] * 1679616 + _ROOT2INT[code[0]] * 60466176
	) if code[0] < 'a' else (
		_ROOT2INT_OLD[code[5]] + _ROOT2INT_OLD[code[4]] * 26 +
		_ROOT2INT_OLD[code[3]] * 676 + _ROOT2INT_OLD[code[2]] * 17576 +
		_ROOT2INT_OLD[code[1]] * 456976 + _ROOT2INT_OLD[code[0]] * 11881376
	))

def int2root(i: int, legacy: bool = False) -> str:
	'''Convert (usually 4 sec) units since epoch to HOPS Mk4 root code

	* `legacy` for `a-z` encoding instead of `0-9A-Z` encoding
	'''
	if legacy:
		chars = 'abcdefghijklmnopqrstuvwxyz'
		s = ''.join(chars[i // 26 ** j % 26] for j in range(5))
		return s + 'abcdefghijklmnopqrstuvwxyz{|}~'[i // 26 ** 5]
	chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
	return ''.join(chars[i // 36 ** j % 36] for j in range(6))

def date2datetime(t: str) -> datetime:
	'''Convert `yyyyddd-HHMMSS` format 000 record date to datetime'''
	return datetime.strptime(t[1:15] if t[0] == ' ' else t[0:14], _FMT_DATE)

def datetime2dtype(t: datetime) -> ndarray:
	'''Convert `datetime` to Mk4 date 0-dim `dtype_date` array

	The return will have lower precision than the original datetime object,
	but it will always be true that: `datetime2dtype(dtype2datetime(t)) == t`
	'''
	v = empty(None, dtype_date)
	v['y'], v['d'] = t.year, (t - datetime(t.year, 1, 1)).days + 1
	v['H'], v['M'] = t.hour, t.minute
	v['S'] = (t.second * 1e6 + t.microsecond) / 1e6
	return v

def dtype2datetime(t: ndarray) -> datetime:
	'''Convert Mk4 date 0-dim `dtype_date` array to `datetime`'''
	us = round(t['S'] * 1e6)
	s = int(3600 * t['H'] + 60 * t['M'] + us // 1000000)
	return datetime(t['y'], 1, 1) + timedelta(int(t['d']) - 1, s, us % 1000000)

def datetime2date(t: datetime, version: int = 0) -> str:
	'''Convert `datetime` to `yyydddd-HHMMSS` format 000 record date string'''
	t = f'{t:{_FMT_DATE}}'.encode()
	return t + b'\x00\x00' if int(version) else b' ' + t + b' '

def get_pol(
	source: Union[str, bytes, BytesIO, Mapping], multi: bool = False
) -> Union[str, Set[str]]:
	'''Polarization mode(s) for an Mk4 fringe file, e.g. 'RR', 'XY', 'I'

	* `source` may be return from `records({203, 205, 208})`
	* `multi` returns all polarizations as a set, e.g. if inconsistent
	'''
	# read source
	source = records(source, (203, 205, 208), True, False)
	pols = set()
	if 208 in source and int(source[208]['record_ver']):
		# look at fourfit -P parameter
		pol = source[208]['unused'][1:2]
		# provisional HOPS code for I
		if pol == b'_':
			return {'I'} if multi else 'I'
		# (not) provisional HOPS code for multi-polarization fringing
		if pol != b'@':
			pol = pol[0] if pol else 0
			if not pol & 0b10000:
				for p, flg in (('XX', 1), ('YY', 2), ('XY', 4), ('YX', 8)):
					if pol & flg:
						pols.add(p)
		# look at pass polarization
		else:
			pol = source[208]['unused'][:1]
			if b'@' <= pol <= b'C':
				pols.add(('XX', 'YY', 'XY', 'YX')[pol[0] - 64])
	# inspect, join records 203 and 205 (usually replaces 208 calulation above)
	if 203 in source and 205 in source:
		ii = list({
			j for i in source[205]['ffit_chans']['chans'] for j in i if j >= 0
		})
		pols = {
			(ref + rem).decode(errors='replace')
			for ref, rem in source[203]['channels'][list(ii)]['polarization']
		}
	# check for inconsistency and return
	if not multi and len(pols) > 1:
		msg = 'inconsistent polarizations: ' + ', '.join(sorted(pols))
		raise ValueError(msg)
	return pols if multi else list(pols)[0]

def get_ps(source: Union[str, bytes, BytesIO, Mapping, void]) -> bytearray:
	'''PostScript plot data from Mk4 type-2 file

	`source` may be return from `records(221, one=True)`
	'''
	if isinstance(source, (str, bytes, BytesIO, Mapping)):
		source = records(source, 221, True)
	return lzrw3a_decompress(bytes(source['ps_plot'][:source['ps_len']]))

def _keep_fringe_stat(st: Mapping, args: argparse.Namespace) -> bool:
	'''Return whether `stat` result matches `argparse` result'''
	if args.B:
		ss = {*st['b'], *st['bl'].split('-'), *st['baseline'].split('--')}
		for bl, f, auto in args.B:
			if None in f or (set(st['band']) & f):
				if auto:
					if len(ss) == 3 and not (bl - ss):
						break
				elif all(s in ss for s in bl):
					break
		else:
			return False
	p = {j for i in args.P or [] for j in i.split(',') if j}
	qcode, ecode = '%d' % st['qcode'], st['ecode'] or '-'
	return not (
		p and st['pol'] not in p
		or args.F and not any(None in f or st['band'] in f for f in args.F)
		or args.E and not any(ecode in (e or '-') for e in args.E)
	 	or args.Q and qcode not in ''.join(args.Q)
		or args.S and st['src'] not in args.S
	)

def ls(path: str = '.') -> Iterable[str]:
	'''List directory as full paths, return `[]` on error'''
	try:
		return [os.path.join(path, f) for f in sorted(os.listdir(path or '.'))]
	except NotADirectoryError:
		return [path or '.']
	except OSError:
		return []

def rls(paths: Iterable[str] = ('.',), _visited: Set = None) -> Iterator[str]:
	'''Recursively list directory or file as full paths, ignore errors'''
	visited = _visited or set()
	for path in paths:
		try:
			st = os.stat(path)
		except OSError:
			continue
		if (st.st_dev, st.st_ino) in visited:
			continue
		if _stat.S_ISDIR(st.st_mode):
			visited.add((st.st_dev, st.st_ino))
			try:
				ll = os.listdir(path)
			except OSError:
				continue
			yield from rls((os.path.join(path, f) for f in sorted(ll)), visited)
		else:
			yield path

def lzrw3a_decompress(
	input: Union[bytes, bytearray], depth: int = 3,
	init: bytes = b'123456789012345678', mult: int = 40543
) -> bytearray:
	'''Decompress an LZRW3-A compressed data block

	* `depth` must match compressor hash table depth bits (0..12)
	* `init` must match compressor initial hash fill
	* `mult` must match compressor hash multiplier constant
	'''
	# read copy flag
	flag, i_input, len_input = input[:4], 4, len(input)
	if not isinstance(flag, bytes):
		raise TypeError('bytes or file in binary mode required: input')
	# no compression if flag = 0x00000001
	if flag == b'\x01\x00\x00\x00':
		return input[4:]
	# compression if flag = 0x00000000
	elif flag != b'\x00\x00\x00\x00':
		flag = ('%02x' * len(flag)) % tuple(reversed(flag)) or '0'
		raise ValueError('invalid copy flag: 0x' + flag)
	# init output and hash table
	i_out, out = 0, bytearray()
	hash = [None] * 4096
	mask = (1 << (12 - depth)) - 1
	dmask = (1 << depth) - 1
	# loop over groups
	control = 1
	cycle = literals = 0
	while i_input < len_input:
		# read next control byte pair (sentinel reached)
		if control == 1:
			control = input[i_input] + 0x100 * input[i_input + 1] + 0x10000
			i_input += 2
		# loop over 1 or 16 groups
		for _ in _RANGE16 if i_input <= len_input - 32 else (0,):
			# copy (hashed) item
			if control & 1:
				# read length and hash index from copy word
				byte_0 = input[i_input]
				n = (byte_0 & 0xf) + 3
				i_hash = input[i_input + 1] | ((byte_0 & 0xf0) << 4)
				i_input += 2
				# grab output index from hash
				i_grab = hash[i_hash]
				# read from init string if hash misses
				if i_grab is None:
					out.extend(init[:n])
				# read from output if hash hits
				else:
					# go one byte at a time in case n > remaining bytes
					# e.g. len = 5; out[i_grab:] = b'he'; result = b'heheh'
					for i in range(i_grab, i_grab + n):
						out.append(out[i])
				# process pending literals (must have been >= 3 from hash)
				if literals:
					# store first output index to cycled hash for 3 chars
					r = i_out - literals
					hash[((((mult * (
						(out[r] << 8) ^ (out[r + 1] << 4) ^ out[r + 2]
					)) >> 4) & mask) << depth) + cycle] = r
					cycle = (cycle + 1) & dmask
					# store second output index to cycled hash for 3 chars
					if literals == 2:
						r += 1
						hash[((((mult * (
							(out[r] << 8) ^ (out[r + 1] << 4) ^ out[r + 2]
						)) >> 4) & mask) << depth) + cycle] = r
						cycle = (cycle + 1) & dmask
					literals = 0
				# store current output index to cycled hash
				hash[(i_hash & ~dmask) + cycle] = i_out
				cycle = (cycle + 1) & dmask
				i_out += n
			# literal byte item
			else:
				# copy to output
				out.append(input[i_input])
				i_input += 1
				i_out += 1
				# store literal index to cycled hash to keep literals < 3
				if literals == 2:
					hash[((((mult * (
						(out[-3] << 8) ^ (out[-2] << 4) ^ out[-1]
					)) >> 4) & mask) << depth) + cycle] = i_out - 3
					cycle = (cycle + 1) & dmask
				# count literals to detect when literals count reaches 3
				else:
					literals += 1
			# get the next control character
			control >>= 1
	return out

def raw_records(
	source: Union[str, bytes, BytesIO], id: Union[int, Iterable[int]] = None,
	enumerate: bool = None
) -> Iterator[Union[void, Tuple[int, void]]]:
	'''Yield records from Mk4 file

	`enumerate`  yield `Mk4ID, record` instead of just `record`
	'''
	# open file
	if isinstance(source, str):
		with open(source, 'rb') as file:
			yield from raw_records(file, id, enumerate)
			return
	elif not hasattr(source, 'readinto'):
		source = BytesIO(source)
	# get IDs
	ids = (None if id is None else set(
		id if isinstance(id, Iterable) else [id]
	))
	# loop over records
	while True:
		# get record type
		d = source.read(5)
		if not d:
			break
		id = int(d[0:3])
		dtype = dtype_record[(id, int(d[3:5]))]
		# read record
		if not ids or id in ids:
			source.seek(-5, 1)
			if isinstance(dtype, _dtype):
				r = empty(1, dtype)
				if source.readinto(r) != dtype.itemsize:
					msg = f'end of file reached while reading record {id:d}'
					raise EOFError(msg)
				r = r[0]
			else:
				r = dtype(source)
			yield (id, r) if enumerate else r
		# skip filtered record
		elif isinstance(dtype, _dtype):
			source.seek(dtype.itemsize - 5, 1)
		else:
			source.seek(-5, 1)
			dtype(source, skip=True)

def recode(path: str, new_root: str, verbose: bool = False):
	'''Change mk4 file root code'''
	# sort out root code and path
	r, is_str = new_root, isinstance(new_root, str)
	broot, sroot = (r.encode(), r) if is_str else (r, r.decode())
	if not _RE_BCODE.match(broot):
		raise ValueError(new_root)
	bare_path, _, ext = path.rpartition('.')
	new_path = (bare_path if _RE_SCODE.match(ext) else path) + '.' + sroot
	if verbose:
		sys.stdout.write(f'{path} > {new_path}\n')
	# copy ovex and log files without editing
	if PATH(path).type in (0, 4):
		shutil.copyfile(path, new_path)
		os.remove(path)
		return
	# open files
	success = False
	with open(new_path, 'wb') as file:
		try:
			# copy ovex and log files without editing
			for rec in records(path):
				# record 000 needs a name change (others do not)
				if rec['record_id'] == b'000':
					fn, _, ext = rec['name'].rpartition(b'.')
					if _RE_BCODE.match(ext):
						rec = rec.copy()
						rec['name'] = (fn + b'.' + broot)[:39] + b'\0'
				# record 100 needs a rootname change (others do not)
				elif rec['record_id'] == b'100':
					fn, _, ext = rec['rootname'].rpartition(b'.')
					if _RE_BCODE.match(ext):
						rec = rec.copy()
						rec['rootname'] = (fn + b'.' + broot)[:39] + b'\0'
				# other records (may) need root_code changed
				elif 'root_code' in rec.dtype.fields:
					rec = rec.copy()
					rec['root_code'] = broot
				# write the record
				file.write(rec)
			success = True
			os.remove(path)
		finally:
			# do not leave partial file on parsing error
			if not success:
				os.remove(new_path)

def recode_auto(dirs: Iterable[str], verbose: bool = False):
	'''Recode mk4 directories in a set to prevent basename collision'''
	# find root files
	roots = {}
	for dir in dirs:
		found, subdirs = False, []
		for name in os.listdir(dir):
			# root file in directory
			if PATH(name).type == 0:
				roots.setdefault(name, set()).add(dir)
				found = True
			# subdirectory
			elif not found:
				if os.path.isdir(path := os.path.join(dir, name)):
					subdirs.append(path)
		if not found:
			for subdir in subdirs:
				#  root file(s) in subdirectory
				for fn in os.listdir(subdir):
					if PATH(fn).type == 0:
						roots.setdefault(fn, set()).add(subdir)
						break
	# now make corrections
	for file, dirs in roots.items():
		if len(dirs) > 1:
			for dir in sorted(dirs)[1:]:
				# find next available code
				orig_code = code = file.rsplit('.', 1)[1]
				new_file, i = file, root2int(orig_code)
				while new_file in roots:
					i += 1
					code = int2root(i)
					new_file = file.rsplit('.', 1)[0] + '.' + code
				# make corrections
				for fn in sorted(os.listdir(dir)):
					if fn.endswith('.' + orig_code):
						recode(os.path.join(dir, fn), code, verbose)

def records(
	source: Union[str, bytes, BytesIO, Mapping],
	id: Union[int, Iterable[int]] = None,
	one: bool = None, required: bool = True
) -> Union[void, Iterable[void], Mapping[int, Union[Iterable[void], void]]]:
	'''Read records from Mk4 file to `dict` like `{ID: [record, ...], ...}`

	* `source` will naively return any mapping object without inspection
	* `one` returns only first records found like `{ID: record, ...}`
	* `required` raises `ValueError` for unfound IDs
	'''
	# convert to set of IDs
	iterable_id = id is None or isinstance(id, Iterable)
	ids = None if id is None else set(id if iterable_id else [id])
	if isinstance(source, Mapping):
		result = source
	# read as scalars
	elif one:
		result = {}
		for i, r in raw_records(source, ids, True):
			if i not in result:
				result[i] = r
				if len(result) == len(ids):
					break
	# read as lists
	else:
		result = {}
		for i, r in raw_records(source, ids, True):
			result.setdefault(i, []).append(r)
		# extract scalars
		if one is None:
			result = {i: r if len(r) > 1 else r[0] for i, r in result.items()}
	# validate and return
	if required and ids and (missing := ids - set(result)):
		msg = ', '.join(f'{i:d}' for i in sorted(missing))
		raise ValueError('record not found: ' + msg)
	return result if iterable_id else result[id]

def ROOTCODE(text):
	'''Validate root code'''
	if not _RE_SCODE.match(text):
		raise ValueError
	return text

def save_ps(
	ps: Union[bytes, bytearray], path: str,
	zoom: float = None, verbose: Union[bool, str] = False
):
	'''Save PostScript to file with a particular zoom level

	`verbose` doubles as source file name if provided as a `str`
	'''
	source = verbose + ' -> ' if isinstance(verbose, str) else ''
	cmd, post = ['gs', '-q', '-dNOPAUSE', '-dBATCH', '-sstdout=%stderr'], []
	cmd += [f'-r{zoom * 96:0.3f}'] if zoom else []
	ext = path.rpartition('.')[2].lower()
	if ext == 'png':
		cmd += ['-sDEVICE=png16m', '-dGraphicsAlphaBits=4', '-dTextAlphaBits=4']
	elif ext in ('jpeg', 'jpg'):
		cmd, ext = cmd + ['-sDEVICE=jpeg'], 'jpeg'
	elif ext == 'tiff':
		cmd += ['-sDEVICE=tiff24nc']
	elif ext == 'pdf':
		cmd += ['-sDEVICE=pdfwrite']
		post = ['-c', '30000000', 'setvmthreshold']
	else:  # eps
		if verbose:
			sys.stdout.write(f'{source}{path} (PS)\n')
		with open(path, 'wb') as file:
			file.write(ps)
		return
	cmd = [*cmd, '-sOutputFile=' + path, *post, '-']
	if verbose:
		sys.stdout.write(f'{source}{path} ({ext.upper()})\n')
	subprocess.run(cmd, check=1, input=ps)

def show(
	recs, tab: str = '    ', indent: str = '',
	suffix: str = '', hanging: bool = False
):
	'''Output human-readable representation of returned mk4 record(s)

	`recs` is the return from `records`
	'''
	ind = '' if hanging else indent
	if isinstance(recs, dict):
		sys.stdout.write(f'{ind}{{\n')
		for k, v in recs.items():
			fmt = '%s%s%03d: ' if isinstance(k, int) else '%s%s%r: '
			sys.stdout.write(fmt % (indent, tab, k))
			show(v, tab=tab, indent=(indent + tab), suffix=',')
		sys.stdout.write(f'{indent}}}{suffix}\n')
	elif isinstance(recs, (types.GeneratorType, list)):
		sys.stdout.write(f'{ind}[\n')
		for v in recs:
			show(v, tab=tab, indent=(indent + tab), suffix=',')
		sys.stdout.write(f'{indent}]{suffix}\n')
	elif isinstance(recs, void):
		sys.stdout.write(f'{ind}(\n')
		for k in recs.dtype.names:
			sys.stdout.write(indent + tab + k + ': ')
			show(recs[k], tab=tab, indent=(indent + tab), hanging=True)
		sys.stdout.write(f'{indent}){suffix}\n')
	else:
		pre = '' if hanging else indent
		recstr = ('\n' + indent).join(repr(recs).split('\n'))
		sys.stdout.write(f'{pre}{recstr}{suffix}\n')

def show_ps(
	ps: Union[str, bytes, Iterable[Union[str, bytes]]], zoom: float = None
):
	'''Show a PostScript plot path or content with GhostScript or Preview

	`ps` treats `str` as path and `bytes` or `bytearray` as content
	'''
	paths, i = [], 0
	with tempfile.TemporaryDirectory('.tmp', 'mk4.') as d:
		# write temp file(s)
		for data in [ps] if isinstance(ps, (str, bytes, bytearray)) else ps:
			if isinstance(data, str):
				paths.append(data)
			else:
				with open(f'{d}/{i}.ps', 'wb') as f:
					f.write(data)
				paths.append(f'{d}/{i}.ps')
				i += 1
		# use open command on Mac OS
		if sys.platform == 'darwin':
			subprocess.run(['open', '-W'] + paths, check=1)
			return
		# use GhostScript otherwise
		env = dict(os.environ)
		env['GS_OPTIONS'] = ((
			'-sDEVICE=x11alpha -dGraphicsAlphaBits=4 '
			'-dTextAlphaBits=4 -dMaxBitmap=50000000 '
		) + env.get('GS_OPTIONS', '')).strip()
		zoom = [f'-r{zoom * 96:0.3f}'] if zoom else []
		subprocess.run(['gs', '-q', '-dBATCH'] + zoom + paths, check=1, env=env)

def stat(
	source: Union[str, bytes, BytesIO, void], path: str = None
) -> Mapping[str, Any]:
	'''Return basic info about an Mk4 fringe file

	* `path` is optional to provide path name if `source` is not a `str`

	Result keys/values are:

	* `exp`: `str` = session name
	* `num`: `int` = HOPS session number
	* `scan`: `str` = scan name
	* `b`: `(str, str)` = baseline (1-char Mk4IDs)
	* `bl`: `(str, str)` = baseline (2-char station IDs)
	* `baseline`: `(str, str)` = baseline (station names)
	* `band`: `str` = frequency bands (`'X'`, `'ABCD'`)
	* `pol`: `str` = polarization (`'XX'`, `'RR'`, `'I'`)
	* `root`: `str` = root code
	* `run`: `int` = HOPS run number
	* `src`: `str` = source name
	* `time`: `datetime` = scan observation start time
	* `ecode`: `str` = error code (`'G'`, `''`)
	* `qcode`: `int` = quality code
	* `dir`: `str` = file path directory
	* `bn`: `str` = file path basename
	* `path`: `str` = file path
	'''
	# read records
	r = records(source, [0, 200, 201, 202, 203, 205, 208], True, False)
	# get and parse path
	path0 = r[0]['name'].partition(b'\0')[0].decode(errors='replace').strip()
	path = path or source and isinstance(source, str) or path0
	parts = os.path.basename(path0).split('.')
	root = parts[-1].strip() or '??????'
	run = parts[-2].strip() or '?' if len(parts) > 1 else '?'
	try:
		run = int(run, 10)
	except ValueError:
		run = 0
	# parse records
	band = ''.join(sorted({
		k[:1].decode()
		for i in r[205]['ffit_chans'] if i['id'].strip()
		for j in i['chans'] if j >= 0
		for k in r[203]['channels'][j]['chan_id']
	}))
	exp = r[200]['experiment_name'].decode(errors='replace').strip() or '?'
	baseline = b'--'.join(r[202]['station_name']).decode(errors='replace')
	return {
		'exp': exp, 'root': root, 'run': run, 'band': band,
		'num': int(r[200]['experiment_number']),
		'scan': r[200]['scan_name'].decode(errors='replace').strip() or '?',
		'b': r[202]['baseline'].decode(errors='replace').strip() or '??',
		'bl': b'-'.join(r[202]['station_id']).decode(errors='replace'),
		'baseline': baseline,
		'pol': ','.join(sorted(get_pol(r, True))) or '?',
		'src': r[201]['source'].decode(errors='replace') or '?',
		'time': dtype2datetime(r[200]['scan_time']),
		'qcode': int(r[208]['quality'].strip() or b'0'),
		'ecode': r[208]['errcode'].strip().decode(errors='replace'),
		'path': path or '?', 'dir': os.path.dirname(path) or '.',
		'bn': os.path.basename(path) or '?'
	}

def main():
	'''Run as script'''
	parser = argparse.ArgumentParser(description=__doc__.partition('\n')[0])
	subs = parser.add_subparsers(dest='action', required=True)
	mkhelp = lambda t: {'help': t, 'description': t.capitalize()}
	# dump
	sub = subs.add_parser('dump', **mkhelp('print human readable records'))
	sub.add_argument(
		'SOURCE', nargs='+',
		help='mk4 file path or mk4 record number to read from file'
	)
	sub.add_argument(
		'-1', '--one', action='store_true',
		help='only display one record of each type from each file (faster)'
	)
	# cf
	sub = subs.add_parser('cf', **mkhelp('print cf file used for fringing'))
	sub.add_argument('SOURCE', nargs='*', help='mk4 fringe file path')
	sub.add_argument(
		'--verbatim', '-v', action='store_true',
		help='output from mk4 file verbatim, usually without any newlines'
	)
	# fix
	sub = subs.add_parser('fix', **mkhelp('fix mk4 name collisions'))
	sub.add_argument('SOURCE', nargs='+', help='mk4 file path')
	sub.add_argument(
		'--verbose', '-v', action='store_true', help='show details'
	)
	# recode
	sub = subs.add_parser('recode', **mkhelp('copy with new root code'))
	sub.add_argument('ROOTCODE', type=ROOTCODE, help='new root code')
	sub.add_argument('SOURCE', nargs='+', help='original mk4 file path')
	sub.add_argument(
		'--verbose', '-v', action='store_true', help='show details'
	)
	# plot
	sub = subs.add_parser(
		'plot', **mkhelp('view or extract plot from Mk4 fringe file(s)'),
		formatter_class=argparse.RawTextHelpFormatter
	)
	sub.add_argument(
		'SOURCE', nargs='*', default=('.',), help='Mk4 type-2 file path'
	)
	sub.add_argument(
		'--verbose', '-v', action='store_true', help='show details'
	)
	sub.add_argument(
		'--ps', '--eps', '-o', dest='save_as', action='store_const', default='',
		const='-', help='print PostScript plot to STDOUT (instead of GUI)'
	)
	try:
		default_zoom = float(os.environ.get('HOPS_ZOOM', '100'))
	except ValueError:
		default_zoom = 100.0
	sub.add_argument(
		'--zoom', '-z', type=float, default=default_zoom,
		help='zoom percent level (default 100)'
	)
	sub.add_argument(
		'--save', '-s', dest='save_as', action='store_const', default='',
		const='{name}.eps', help='alias for --save-as={name}.eps'
	)
	sub.add_argument('--save-as', metavar='PATH', help=(
		'save to PostScript file path, expands {} for the following:\n'
		f'  {{name}}      {_FMT_PS_NAME.replace("%", "%%")}\n'
		'  {exp}       experiment session (e.g. I20001)\n'
		'  {num}       experiment number (e.g. 1234)\n'
		'  {b}         HOPS format baseline (e.g. KV)\n'
		'  {bl}        standard format baseline (e.g. Kk-Wz)\n'
		'  {baseline}  long-format baseline (e.g. KOKEE-WETTZELL)\n'
		'  {band}      frequency band (e.g. X)\n'
		'  {pol}       polarization (e.g. RR, XY)\n'
		'  {root}      root code (e.g. abcdef)\n'
		'  {run}       run number (e.g. 5)\n'
		'  {src}       radio source (e.g. 0059+581)\n'
		'  {path}      Mk4 file path\n'
		'  {dir}       Mk4 file directory\n'
		'  {bn}        Mk4 file basename\n'
	))
	grp = sub.add_argument_group('filtering arguments')
	grp.add_argument(
		'-B', '-b', type=BASELINE, action='append', metavar='AB:X',
		help='baseline and (optionally) frequency band'
	)
	grp.add_argument(
		'-F', type=BAND, metavar='X', action='append', help='frequency band'
	)
	grp.add_argument(
		'-P', metavar='POL', action='append',
		help='polarization product (e.g. XX, RR, I)'
	)
	grp.add_argument(
		'-E', metavar='ECODE', action='append',
		help='error code(s) (? any errors, - for no errors)'
	)
	grp.add_argument(
		'-Q', metavar='QCODE', action='append', help='quality code(s)'
	)
	grp.add_argument(
		'-S', metavar='SRC', action='append', help='radio source name'
	)
	args = parser.parse_args()
	# fix
	if args.action == 'fix':
		recode_auto(args.SOURCE, args.verbose)
	# recode
	elif args.action == 'recode':
		for path in args.SOURCE:
			recode(path, args.ROOTCODE, args.verbose)
	# plot
	elif args.action == 'plot':
		plotted, pss = 0, []
		paths = [p for p in rls(args.SOURCE) if PATH(p).type == 2]
		for i, src_path in enumerate(paths, 1):
			if args.save_as != '-':
				sys.stderr.write(f'\rplotting fringe {i} / {len(paths)}')
				sys.stderr.flush()
			# read records and filter scan
			r = records(src_path, [0, 200, 201, 202, 203, 205, 208, 221], 1)
			st = stat(r, src_path)
			if _keep_fringe_stat(st, args):
				plotted += 1
				# get and save/print eps file
				ps = get_ps(r[221])
				if args.save_as not in '-':
					st['name'] = _FMT_PS_NAME.format(**st)
					path = args.save_as.format(**st)
					verbose = src_path if args.verbose else False
					save_ps(ps, path, args.zoom * 0.01, verbose)
				elif args.save_as == '-':
					try:
						sys.stdout.buffer.write(ps)
					except BrokenPipeError:
						sys.stderr.close()
						raise
				# remember to plot
				else:
					pss.append(ps)
		if args.save_as != '-':
			sys.stderr.write(f'\r\033[Kread {len(paths)} fringe files\n')
		# plot all at once (better than 1 at a time for GhostScript)
		if pss:
			sys.stderr.write(f'plotting {len(pss)} fringes\n')
			show_ps(pss, args.zoom * 0.01)
		else:
			sys.stderr.write(f'plotted {plotted} fringe files\n')
	# cf
	elif args.action == 'cf':
		paths = args.SOURCE or ['-']
		stdin = sys.stdin.buffer.read() if '-' in paths or '' in paths else b''
		for path in paths:
			try:
				cf = records(stdin if path in '-' else path, 222, True)['cf']
			except ValueError:
				sys.stderr.write(f'no cf record: {shlex.quote(path)}\n')
				continue
			if args.verbatim:
				sys.stdout.buffer.write(cf.partition(b'\0')[0])
				continue
			cf = cf.decode(errors='replace')
			try:
				import vlbi.cf  # do this here because it may be missing
				cf = str(vlbi.cf.CF().read_text(cf))
			except (ImportError, ValueError):
				cf = cf if cf.endswith('\n') else cf + '\n'
			try:
				sys.stdout.write(cf)
			except BrokenPipeError:
				sys.stderr.close()
				raise
	# dump
	else:
		# separate IDs and paths
		ids, paths = set(), []
		for src_path in args.SOURCE:
			try:
				ids.add(int(src_path, 10))
			except ValueError:
				paths.append(src_path)
		if not (paths or sys.stdin.isatty()):
			paths.append('-')
		if not paths:
			parser.error('source path required')
		ids_str = ', '.join(map(str, sorted(ids)))
		ids_str = ids_str and f', [{ids_str}]'
		# read STDIN
		if '-' in paths or '' in paths:
			stdin = sys.stdin.buffer.read()
		# loop over paths
		for path in paths:
			one = ', one=True' if args.one else ''
			# read file
			if path in '-':
				p = 'sys.stdin'
				r = records(stdin, ids or None, args.one)
			else:
				p = repr(path)
				with open(path, 'rb') as file:
					file = file.read()
			# show file
			try:
				sys.stdout.write(f'records({p}{ids_str}{one}) = ')
				show(records(file, ids or None, args.one))
			except BrokenPipeError:
				sys.stderr.close()
				raise

if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt:
		sys.stdout.write('\n')
