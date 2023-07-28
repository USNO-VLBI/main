# VLBI Correlation Support Software

## System Requirements

* Operating System (OS):
  * Linux (tested under RHEL 7, Ubuntu 18, and Ubuntu 20), or
  * MacOS (tested under OSX 10.15), or
  * partial support for Windows (some modules will work in Windows 10)
* Python 3.8 or later with `numpy`, `scipy`, `matplotlib`, and `pandas`
  * Ubuntu: `apt install python3-{numpy,scipy,matplotlib,pandas}` covers everything
  * For RHEL, MacOS or Windows, a standard Anaconda python environment should work
* NASA SKED
  * only required for ad-hoc SNR file generation, not necessarily for some users

## Installation

1. Install dependencies

   * Ubuntu, Debian:
     * `apt install python3-{numpy,scipy,matplotlib,pandas}`
     * Or install Anaconda as with Windows, MacOS, RHEL
   * Windows, MacOS, RHEL:
     * Install Anaconda with standard environment or miniconda + `pandas`

2. Unpack files to directory of your choice, common choices are:

   * `/correlator`
   * `/opt/correlator`
   * `~/correlator`
   * `~/opt/correlator`

3. Add paths to your `.bash_profile` or `.bashrc`:

   ```bash
   export CORRPATH=/correlator
   export PATH="$CORRPATH/bin:$PATH"
   export PYTHONPATH="$CORRPATH/pylib"
   ```

4. *(Optional)* Pre-compile modules for better performance:

   ```bash
   python3 -m compileall "$CORRPATH/pylib"
   ```

## Package Contents

### `pylib/vlbi/__init__.py`

Entry point to main `vlbi` Python module

This module initialization script provides several helper scripts to the rest of the module. Most importantly, it finds the `ROOT` directory for the  correlator, which is defined by either:

* The `$CORRPATH` environment variable, or
* A `.CORRROOT` breadcrumb file in a driectory above the `vlbi` Python module level

### `pylib/vlbi/catmap.py`

Map catalog names to offical names

This module accounts for differences between the names used for stations and sources by different VLBI networks.

### `pylib/vlbi/cf.py` and `bin/cf`

Read HOPS fringing configuration (CF) file

This module allows interpretation of the CF file, which is important for formatting it in a standardized way for the report, since it comes in a single long line of text from the Mk4 fringe files.

### `pylib/vlbi/fit_fmout.py` and `bin/fit-fmout`

Read station logs and calculate maser clocks

These logs come in a dizzying variety of formats. They contain a great deal of information, but the most important is the offsets between the station's hydrogen maser atomic clock and a longer-term stable source of truth such as a GPS reciever.

### `pylib/vlbi/master.py` and `bin/master`

Read master schedule files

These list which sessions are being observed by which stations, who will schedule, correlate, and analyze them, and when they are observed/processed.

### `pylib/vlbi/mk4.py` and `bin/mk4`

Read HOPS Mk4 fringing binary files

The HOPS Mk4 binary files contain all the actual data we care about encoded in HOPS-specific binary records formats.
This module allows Python to read, edit, and write these files. The report generator normally only reads the files.

This module contains my own Python implementation of Ross Williams' LZRW3-A compression algorithm, and the only Python implementation I'm aware of.
HOPS itself lacks the ability to read it's compressed data, because it only has the compressor and not the decompresser implemented.

### `pylib/vlbi/report.py` and `bin/report`

Read and write VLBI correlator report

This module ties together all the other modules in this package plus many more file formats to produce a wholeistic report of the correlation for human and machine readability. It can also extract existing reports from VGOSDB tarballs. (Note: NetCDF is not used, since the reports are plain text within the VGOSDB tarball.)

### `pylib/vlbi/stations.py` and `bin/stations`

Read and write `stations.m`, `m.stations`, and `ns-codes.txt` stations listing files

Each network has its own `ns-codes.txt`, and each correlator maintains one or more `stations.m` and/or `m.stations` files.
This module allows for reading all of them and merging them into a usable Python dictionary-like object.

## Input and Output Files

All files are in `text/plain` format unless otherwise stated in descriptions.
Files are generally in UTF-8 encoding, and the `replace` (errors are replaced by `?` or similar) or `ignore` (errors are quietly removed) methods are used for non-UTF-8 compliant input data.

File                       | Operation  | Used By                      | Description
---------------------------|------------|------------------------------|------------------------------------------------
`?..??????`                | read/write | `mk4`                        | (binary) HOPS Mk4 station description
`??..??????`               | read/write | `mk4`                        | (binary) HOPS Mk4 correlation visibilities
`??.?.?.??????`            | read/write | `report`, `mk4`              | (binary) HOPS Mk4 fringe
`*.calc`                   | read       | `report`                     | DiFX/CALC correlation scan timing model
`*.corr`                   | read/write | `report`                     | Correlation report
`*.eps`                    | write      | `mk4`                        | Output plot
`*.input`                  | read       | `report`                     | DiFX correlation scan configuration
`*.log`                    | read       | `report`, `fit-fmout`        | Station observation logs
`*.png`                    | write      | `mk4`, `fit-fmout`           | Output plots
`*.snr`                    | read/write | `report`                     | SKED signal to noise (SNR) predictions
`*.tgz`                    | read       | `report`                     | VGOSDB archive (tarball of `text/plain` and binary NetCDF files)
`*.vex`, `*.ovex`          | read       | `report`, `fit-fmout`, `mk4` | VLBI session schedule
`cat-map`                  | read       | `report`, `catmap`           | Catalog name mappings
`cf`                       | read       | `report`, `cf`               | HOPS fringing configuration
`clock.offset`             | read       | `report`, `fit-fmout`        | Peculiar offsets for station clocks
`master*.txt`              | read/write | `report`, `master`           | VLBI master schedule
`ns-codes.txt`             | read/write | `report`, `stations`         | List of station names, IDs, and other details
`skd`                      | read       | `report`, `fit-fmout`        | SKED format VLBI session schedule
`stations.m`, `m.stations` | read/write | `report`, `stations`         | Map of station ID to HOPS ID
