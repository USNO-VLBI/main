# IVS Memorandum 2022 | Correlator Report

## Introduction

The correlator report is intended to help stations and analysts to read and understand the correlator output. This document defines the standard for these correlator reports. The report is fully human-readable and mostly machine-readable. Reports are expected to be mostly automatically generated with little or no manual editing. It is recommended that all IVS correlators follow the standard, with the caveat that some deviations may be necessary under special circumstances. The correlator report is distributed over email (e.g. to IVS mailing lists), web (CDDIS data centers), and always as part of the VGOSDB archive (by using the report flag argument to `vgosDbMake`).

To ease the readability of the correlator reports for IVS experiments, correlator representatives from Haystack, Washington, Bonn, Chalmers and Vienna met at the Technical Operation Workshop (TOW) in 2019 to re-define, clarify and simplify the standard. Additional adjustments to the format to accommodate VGOS sessions in a uniform standard were discussed in February 2022 at the IVS correlator Telecon, where the effort was revived.

## Format

The report is intended to provide human- and machine-readable information about the correlation process and results. It maintains some level of compatibility and continuity with previous IVS reports, but diverges significantly in some respects.

### Magic Numbers

The first line of the correlator report must contain the exact text:

```
%CORRELATOR_REPORT_FORMAT 3
```

The above line identifies the file (or block of a file) as a correlator report and additionally identifies the format version being used. The format version numbers are as follows:

Version Number | Format Document
:-------------:|:-------------------------:
`0`            | None
`1`            | IVS Memorandum 2017-001v01
`2`            | IVS Memorandum 2017-001v02
`3`            | this document

The final line of the correlator report must contain the exact text:

```
+END
```

The above `+END` line doubles as an empty section.

### General Format

The sections listed in this document should be included whenever possible, unless marked as optional. Additionally, you may include your own custom sections, as long as they obey the general rules for a dictionary, table, or text section.

Follow these rules when generating the report:

* Encode as **UTF-8** plain text
  * Restrict to UTF-7 (ASCII) character set whenever practical
  * An alternate encoding may be used only when sending as the body text of an email or serving as a web page
    * The alternate encoding must be specified in the MIME header, e.g. `Content-Type: text/plain; charset=utf-16`
    * Conversion to and from an alternate encoding must be lossless
* Use single LF line feed (`\n`) character at the end of every line
  * The only exception to this is when sending as the body text of an email, in which case the CRLF carriage return and line feed (`\r\n`) newline sequence may be used
* Do not include any non-printable characters except space and the above specified new line
  * This means no carriage return (`\r`) or tab (`\t`) characters
* There is no hard line width limit
  * Try to use no more than 80 characters per line if possible and practical
  * Try to use no more than 120 characters per line if you need to go over 80
* Use `yyyy-ddd-HHMM` or `yyyy-ddd-HHMMSS` format for all dates and times
  * This is one of the formats specified in the RFC3339 profile of ISO8601
  * Report all dates and times in the **UTC time zone** using the Gregorian calendar system
* Round values using the "**round half to even**" method if possible
  * This is the default for IEEE 754 floating point values
  * The "round half to odd" method is also acceptable
  * Do **not** use the "round half up", "round half down", "round half toward zero", or "round half away from zero" methods; they introduce statistical bias

Use these standard formats for values:

type              | format              | example(s)
------------------|---------------------|------------------------------------------------------------
N/A or Missing    | `-`                 | `-`
Scalar Integer    | decimal integer     | `15`<br>`-55`<br>`+175`
Scalar Real       | decimal real        | `-15.670`
Scalar Real       | scientific notation | `1.0e5`<br>`14.100e+03`
Scalar Fractional | decimal percent     | `70.00%`
Text              | unquoted string     | `foo`, `bar baz`
Measure           | scalar real & units | `16.504 usec`
List              | comma-separated     | `foo, bar, baz`<br>`55.0 mm, 1.0 m, 150 mm`<br>`1, 2, 3, 4`

When writing these values:

* Use units from the current VEX standard where possible
* Lists may contain any of the other types inside them

### Section Format

The report consists of several sections. Each section is dedicated to communicating a particular aspect of the correlation process. The sections all follow these rules:

* Each section begins with a section header consisting of a line starting with the `+` character and the section name, for example `+THIS_SECTION`
* Section names must be a series of uppercase UTF-7 (ASCII) alphanumeric characters and underscore(s) (`A`-`Z`, `0-9`, and `_`)
* The next line after the section header should be left blank
* No other lines in the report may start with a `+` character in the first column
* The `+HEADER` section should come first, and the `+END` "section" must come last
  * Other sections may be included in any order
* A section may be included multiple times if and only if the multiple sections represent multiple configurations used during the session
  * An example may be multiple `+FRINGING_CONFIG_FILE` sections when more than one control file was used
  * In such cases, a comment in `+NOTES` explaining when each control file was used is highly recommended

There are three general types of sections: dictionary, table, and text.

### Format of Dictionary Sections

Dictionary sections are a sequence of key value pairs.

* Each line begins with a key name, which does not include any whitespace characters
* The key name is separated from the value(s) by one or more space
* Key names should be uppercase if possible
  * Site IDs are examples of cases where mixed case is required instead of uppercase
* If the key name contains `TEXT`, `STRING` or `VERSION` (case insensitive), then the value is always interpreted as either text or list of text

Generic example:

```
SHAPE        circle
LENGTH       100 mm
COORDINATES  15 mm, -134 mm
```

### Format of Table Sections

Table sections are a set of tabular data formatted with a header, body, and an optional legend at the end.

* The header and data rows are space-separated values
  * These values may be any type besides measure, but may not include any spaces
  * Measures are represented as scalars in the table, and their units are specified in the legend
* The first table line is a space-separated list of column names
* The second table line is a sequence of two or more `-` characters and separates the column names from the data
* Subsequent lines contain data values
  * They must include a `-` character to indicate any missing, unavailable, or not-applicable data entry
  * The right-most `-` entries in a row may be omitted
  * Blank lines are not parsed, and can be used to visually group related lines
* Column names and data rows must not begin with a space or a `*` character
* An optional legend may be included after the table
  * An empty line is included between the table and its legend
  * Each legend entry consists of a `*` character and a space, a column name or range (without spaces), one or more spaces, optional units inside `()`, and a one-line description of the column's meaning
  * Any column containing a measure should include the units in parentheses to ensure parsers can convert from the scalar table value to the applicable measure(s)
  * VEX compatible units should be used whenever possible
* Only the last column of the table may contain any whitespace
  * This last column can be used, for instance, for a text comment as in the `+NOTES` section

Generic example:

```
shape   length  x_coord  y_coord  comment
--------------------------------------------------------------
circle     100     15.0   -134.0
square      50      0.0     14.5  technically also a rectangle
point        -   -100.0     50.5

* shape    name of the shape
* length   (mm) length or diameter of the shape
* x_coord  (mm) X coordinate
* y_coord  (mm) Y coordinate
* comment  additional details
```

### Format of Text Sections

Text sections are just plain, freeform text, usually as a way to embed configuration or data files from other sources.

* Text section names must end in `FILE` or `TEXT` (e.g. `CORRELATION_CONFIG_FILE`)
* Text sections comply with the general section rules, such as UTF-8 encoding and line feed (`\n`) new lines
  * This includes the requirement that lines **not** start with a `+` character

## `+HEADER` Section

This **mandatory dictionary section** contains general session information.

```
+HEADER

SESSION     A12345
VGOSDB      20JAN31AA
START       2022-031-1830
END         2022-032-1830
CORRELATOR  WACO
ANALYST     Jane Doe, Mei Sato
VERSION     1-1
```

Key          | Type     | Description
-------------|----------|------------
`SESSION`    | text     | Session code<br>Defined in the [master](https://cddis.nasa.gov/archive/vlbi/ivsformats/master-format.txt) control file<br>Note: This is the session code, **not** the session name!
`VGOSDB`     | text     | IVS VGOSDB name<br>Defined in the [`master-format.txt`](https://cddis.nasa.gov/archive/vlbi/ivsformats/master-format.txt) control file in the DBC code section<br>Do **not** include any `$` prefix
`START`      | datetime | Session schedule start time<br>Defined in the [master](https://cddis.nasa.gov/archive/vlbi/ivsformats/master-format.txt) control file
`END`        | datetime | Session schedule end time<br>Defined in the [master](https://cddis.nasa.gov/archive/vlbi/ivsformats/master-format.txt) control file
`CORRELATOR` | text     | IVS correlator identification code<br>Defined in the [`master-format.txt`](https://cddis.nasa.gov/archive/vlbi/ivsformats/master-format.txt) control file in the `CORR` section<br>Use the same capitalization as is used in the control file
`ANALYST`    | text     | Name(s) of correlator analyst(s) who correlated/fringed the session
`VERSION`    | text     | Semantic version in `correlation-fringing` format<br>The first released fringing of first released correlation is version `1-1`

The `VERSION` semantic version number is a string tracking which correlations and fringings have been finalized and released by the correlator. They are in `correlation-fringing` format and follow these conventions:

* Both counts start at `1` for the first released VGOSDB
* Increment the correlation count for each *released* re-correlation
  * The fringing count resets to `1` when the correlation count increments
* Increment the fringing count for each *released* re-fringing
* Do **not** increment the correlation or fringing count for intermediary correlations or fringings
  * Test passes, clock passes, and unreleased runs do not increment the counts
* Do **not** increment the counts when re-releaseing an existing correlation and fringing
  * Releasing with a new VGOSDB format or procedure does not increment the counts
* Use a `0` for either the correlation or version count when exporting a VGOSDB for informal use
  * Examples would be to analyze without releasing, or an unofficial report

## `+SUMMARY` Section

This **table section** summarizes data quality and processing. Percentages in the table are calculated on a per baseline and per scan basis.

```
+SUMMARY

qcode     total  correlated
---------------------------
5-9       70.00%     90.00%
0          4.00%      6.00%
1-4,A-H,N 22.00%      2.00%
removed    1.00%      0.00%

* qcode       quality codes, error codes, or status
* total       percent of total scans
* correlated  percent of correlated scans
```

Column       | Description
-------------|------------
`qcodes`     |fringe quality code(s) as defined by fringing software (e.g. HOPS)
`total`      |percent of all scheduled baseline scans with the specified quality code(s)
`correlated` |percent of all attempted-correlated baseline scans with the specified quality code(s)

The rows may vary depending on your fringing software, but should contain at least the `0` and `removed` rows, and should contain at least one row each for unsuccessful/low-quality data and high-quality data.

Row Name    | Description
------------|------------
`5-9`       | baseline scans without errors and with quality codes from `5` through `9`
`0`         | baseline scans without errors, but also without a fringe found<br>(use `0` here even for non-HOPS fringing software if possible)
`1-4,A-H,N` | baseline scans with errors, or with "low" quality codes from `1` through `4`<br>(use `N` for unexplained failure of correlation, fringing, or data transmission)
`removed`   | baseline scans which were not observed, or which were not transmitted or correlated for known and documented reasons

Data that are **unrecoverable** for known reasons, **not observed**, or **not transferred** to the correlator count as **`removed`**; reasons for removing data may include:

  * station did not observe
  * lost antenna pointing control
  * antenna stowed in high wind or heavy precipitation
  * failed data recording system
  * lost or damaged diskpack

These `removed` data are also counted in the `+QCODES` table's `-` column.

## `+STATIONS` Section

This **mandatory table section** lists the station codes, names, and ID numbers.

```
+STATIONS

station name     mk4
--------------------
Is      ISHIOKA  I
Kk      KOKEE    K
Wz      WETTZELL v

* station  2-char station ID
* name     3- to 8-char station name
* mk4      1-char HOPS station code
```

Column    | Description
----------|------------
`station` | 2-character station ID from the [`ns-codes.txt`](https://cddis.nasa.gov/archive/vlbi/ivscontrol/ns-codes.txt) control file
`name`    | 3- to 8-character station name from the [`ns-codes.txt`](https://cddis.nasa.gov/archive/vlbi/ivscontrol/ns-codes.txt) control file
`mk4`     | 1-character HOPS station IDs used in the `fourfit` control file<br>*This column may be omitted if the fringing software does not use custom IDs*

## `+NOTES` Section

This **table section** provides notes on correlation and fringing results.

```
+NOTES

station  note
----------------------------------------------------------------
-        Final release with Ht data

Mc       Stopped to observe another session 032-1808 -- 032-1830
Mc       Removed channel from fringe fitting: SR6U
Mc       Applied manual phase calibration

Ny       Halted 032-1800 -- 032-1808

Sa       Antenna problem 032-1606 -- 032-1628

Ur       Not correlated
Ur       Data could not be recovered from disk pack

Yg       Applied manual phase calibration

Zc       Stopped to observe intensive 032-0650 -- 032-0802
Zc       Removed channel from fringe fitting: SR2U, SR3U

Ns-Ny    Notch filters applied

Ht-Is-Mc Closure ambiguities

* station  2-char station ID, baseline, closure set, or - for general notes
* note     correlator notes and feedback
```

Column   |Description
---------|-----------
`station`|`-` for a general **correlator note**<br>`Id` for a note about a specific **station** *(where `Id` is the station ID)*<br>`Id-Id` for a note about a specific **baseline** *(where `Id`s are station IDs)*<br>`Id-Id-Id` for a note about a specific **closure** set *(where `Id`s are station IDs)*
`note`   |single-subject text note or description of an issue

How to use this section:

* Stations without any issues or notes do not need to be included here
* Each row should contain only a single note or issue
* You can include multiple rows with the same `station` column value
  * E.g. for multiple station, general, or baseline notes
  * Optionally use blank lines to group matching rows together
* Use simple, plain-spoken, specific wording to help with translation
* Be concrete and specific; avoid speculating or editorializing
* Express time ranges, channels, and other lists as comma-separated lists
* Use the templates below when applicable to help with automated parsing

Here is a list of common notes and text you can use. You don't *have* to use these templates, but using them can help make your `+NOTES` section easier for automated scripts to parse. Remember to replace times, session names, channel names, and other values with the correct values from your session.

```
-        Preliminary release without station: St, St
-        Re-release with station: St, St

Id       Clock break at yyyy-ddd-HHMM (0.000 usec)

Id       Did not observe
Id       Late start
Id       Stopped to observe SESSION
Id       Data have not arrived at correlator yet
Id       Data could not be recovered from disk pack
Id       Not correlated

Id       No fringes found
Id       Large number of non-detections
Id       Low fringe quality codes
Id       Low fringe amplitude in channel: XR0U, XR1U
Id       Removed channel from fringe fitting: XR0U, XR1U
Id       Low phase calibration amplitudes
Id       Poor closure before manual phase calibration
Id       Applied manual phase calibration
Id-Id    Notch filters applied
Id-Id    Noisy results from colocated antenna
Id-Id-Id Closure ambiguities
Id-Id-Id Poor closure
```

In the above, you may add specific time ranges with a `:` followed by a comma separated list of scan times or time ranges. Using a `(start)` prefix for the beginning of the session or `(end)` suffix for the end of the session will help with readability too.

Here are some examples:

```
Id Late start: 2022-001-1955
Id No fringes found: (start) 2020-001-1830 -- 2020-002-0630
Id Stopped to observe X20001: 2020-002-1700 -- 2020-002-1830 (end)
```

## `+CLOCK` Section

This **table section** describes the clock offsets used for each station.

```
+CLOCK

st epoch           used-offset     used-rate  raw-offset    raw-rate    comment
--------------------------------------------------------------------------------------
Ht 2022-031-183000      8.014   5.797000E-14       5.114000   5.797000E-14
Is 2022-031-183000      0.614  -5.412000E-14       0.312000  -5.412000E-14
Kk 2022-031-183000     10.374  -4.017000E-13       9.857000  -4.017000E-13
Kk 2022-032-063000     10.357  -4.017000E-13        -          -           clock-break
Mc 2022-031-183000     -9.588  -5.356000E-14     -11.271000  -5.356000E-14
Ns 2022-031-183000     24.738   3.679000E-13      22.865000   3.679000E-13
Ny 2022-031-183000    -85.923  -4.449000E-13     -88.235000  -4.449000E-13
Sa 2022-031-183000      1.678   7.533000E-14      -0.362000   7.533000E-14
Wz 2022-031-183000     -4.957   2.973000E-14      -7.121000   2.973000E-14 reference
Yg 2022-031-183000      7.784   2.375000E-12       5.507000   2.375000E-12
Zc 2022-031-183000    214.970  -4.213000E-13      -1.678000  -4.213000E-13

* st           2-char station ID
* epoch        time coordinate of offsets and clock model segment start time
* used-offset  (usec) station clock minus offset used in correlation at epoch
* used-rate    drift rate of station clock minus offset used in correlation
* raw-offset   (usec) station clock minus reference clock offset at epoch
* raw-rate     drift rate of station clock minus reference clock offset
* comment      clock-break, reference station, or other notes
```

Column        | Type             | Description
--------------|------------------|------------
`st`          | text             | 2-character station ID code from the `ns-codes.txt` control file<br>The same station ID may be used multiple times in the case of a clock break
`epoch`       | datetime         | datetime at which the clock offsets and rates are considered valid<br>Also and epoch of the row's reported clock offset values<br>Should be the beginning of the session for non-clock-break lines
`used-offset` | number (usec)    | Station clock minus reference clock offset adjustment used by the correlator at the `epoch`
`used-rate`   | number (sec/sec) | Drift rate of station clock minus reference clock offset used in correlation
`raw-offset`  | number (usec)    | Station clock minus reference clock offset at the `epoch` measured by station calibration equipment
`raw-offset`  | number (usec)    | Station clock minus reference offset at the `epoch`, typically measured as the time interval from the station DOT 1 PPS to the reference clock (usually GPS) 1 PPS, absolute value ≤ 0.5 sec
`raw-rate`    | number (sec/sec) | Drift rate of station clock minus reference clock offset measured by station calibration equipment
`comment`     | text             | Space-separated list of informative tokens to include:<br>`reference` to indicate the clock reference station<br>`clock-break` to indicate epochs where a discontinuity occurs<br>May also contain a plain text comment.<br> Must only contain a token from above if the token's meaning is intended (e.g do **not** write a comment like "`no clock-break this time`").

Stations typically use a GPS Time (GPST) receiver as a reference clock. This reference time is converted to the UTC timestamp format (e.g. by accounting for leap seconds). GPS Time signals track very closely with UTC and are used as a proxy for UTC.

Note that the report `+CLOCK` section table rows and the VEX `$CLOCK` section `clock_early` parameters are closely related.

`+CLOCK` Column | VEX `clock_early` Field                 | Relationship
----------------|-----------------------------------------|-------------
`epoch`         | 1 (validity epoch) and 3 (origin epoch) | `epoch` ≥ validity epoch
`used-offset`   | 2 (clock offset)                        | `used-offset` = clock offset + clock rate • (`epoch` - origin epoch)
`used-rate`     | 4 (clock rate)                          | `used-rate` = clock rate
`raw-offset`    | 7 (fmout2gps)                           | `raw-offset` = fmout2gps + `raw-rate` • (`epoch` - origin epoch)
`raw-rate`      | No VEX equivalent                       | N/A

It is ***not required***, but it may be helpful for book keeping if the VEX file `clock_early` values match the report table value counterparts verbatim. To achieve this, you can *(optionally)*:

* Always use the same value for `clock_early` field 1 (validity epoch) and field 3 (origin epoch)
* Set `clock_early` epochs to the start of the session for initial clocks
* Set `clock_early` epochs to the start of the clock break for clock breaks

## `+CHANNELS` Section

This **table section** maps HOPS Mk4 fringe file channel names to sky frequencies in MHz. The HOPS channel names may be combined if using the same sky frequency by adding a `/` followed by an alternate suffix. E.g. `X00LX/Y` is equivalent to `X00LX` and `X00LY`. The table must be sorted in ascending numerical order by sky frequency first, then ascending lexical order by HOPS channel name. If using a fringing software besides HOPS, then use the closest available analog to the HOPS channel names.

```
+CHANNELS

channel id frequency
--------------------
S00UR   a    2225.99
S01UR   b    2245.99
S02UR   c    2265.99
S03UR   d    2295.99
S04UR   e    2345.99
S05UR   f    2365.99
X06LR   g-   8212.99
X06UR   g+   8212.99
X07UR   h    8252.99
X08UR   i    8352.99
X09UR   j    8512.99
X10UR   k    8732.99
X11UR   l    8852.99
X12UR   m    8912.99
X13LR   n-   8932.99
X13UR   n+   8932.99

* channel    HOPS channel name
* id         short name with sideband indicator
* frequency  (MHz) sky frequency
```

## `+DROP_CHANNELS` Section

This **dictionary section** lists the channels which were not included in fringe fitting.

```
+DROP_CHANNELS

Mc     SR6U
Zc     SR2U, SR3U
Ns-Ny  SR2U
```

In the `+DROP_CHANNELS` table:

* Keys names are station ID codes from the [`ns-codes.txt`](https://cddis.nasa.gov/archive/vlbi/ivscontrol/ns-codes.txt) control file, or baselines in `Id-Id` format
* Values are lists of the HOPS names of channels which were removed from fringe fitting across all baselines with that station present
* The reason for each channel removal should be enumerated in the `+NOTES` section

## `+MANUAL_PCAL` Section

This special **dictionary section** has only keys and no values. The keys are the 2-character station ID codes from the [`ns-codes.txt`](https://cddis.nasa.gov/archive/vlbi/ivscontrol/ns-codes.txt) control file of stations for which a manual phase calibration was applied during fringe fitting.

```
+MANUAL_PCAL

Mc
Yg
```

Please note that manual phase calibrations should ***only*** be used when they materially improve the resulting products, e.g. by improving closure. They are **not advised for VGOS** observations. Correlator and analysis centers should work together to ensure that manual phase calibrations are only done when needed.

## `+QCODES` Section

This **table section** summarizes the fringe quality and error codes as calculated by HOPS `fourfit`. The table may be produced by HOPS `aedit` command `psfile`, or generated from the `alist` file by a script.

```
+QCODES

bl:band  0 1 2 3 4 5  6   7    8     9   G  H    N   - total
------------------------------------------------------------
JI:S    11 0 0 0 0 0  0   0    1    56   0  0    0   0    68
JI:X    19 0 0 0 0 0  0   0    2    47   0  0    0   0    68
Jc:S     2 0 0 0 0 0  0   0    3   138   1  0    0   3   147
Jc:X     3 0 0 0 0 0  0   0    3   138   0  0    0   3   147
Jb:S    37 0 0 0 0 0  4   2    3    29   0  0    0   0    75
Jb:X    22 0 0 0 0 0  0   0    3    49   1  0    0   0    75
[...]
total  949 0 0 0 0 7 71 296 2459 10826 383 23 4014 288 19316

* bl:band  baseline and frequency band name
* 0        no fringe detected
* 1-9      fringe detected, higher value means better quality
* B        fourfit interpolation error
* D        no data in one or more frequency channels
* E        fringe found at edge of SBD, MBD, or rate window
* F        fork problem in processing
* G        channel amplitude diverges too far from mean amplitude
* H        low phase-cal amplitude in one or more channels
* N        correlation or fringing failed
* -        correlation not attempted
* total    column and row totals
```

Column    | Description
----------|------------
`bl:band` | baseline and band using the two 1-character HOPS station IDs, `:`, and the 1-char band name
`0` – `9` | baseline scan count for non-error quality codes `0` through `9`
`A` – `H` | baseline scan count for error codes `A` through `H`<br>*one or more of these may be omitted if no such error codes are present*
`N`       | count of baseline scans which failed to process
`-`       | count of baseline scans which were not processed ("minused") for know reasons
`total`   | total count of all baseline scans in all the other columns

The final `total` row lists the sums of all previous rows

## `+SNR_RATIOS` Section

This **table section** shows the observed vs expected signal-to-noise ratios for each baseline over all sources in the session. Reported observed / expected SNR values near 1.0 indicate accurate expected SNR values, above 1.0 indicate underestimated expected SNR values, and below 1.0 indicate overestimated expected SNR values.

```
+SNR_RATIOS

bl         S  n_S         X  n_X
--------------------------------
JI  0.851234   56  0.741145   49
Jc  1.367274  142  0.835524  141
JN  1.192573   77  0.452345   38
Jb  0.465863   37  0.462345   53
[...]

* bl       baseline
* [A-Z]    ratio for this band name
* n_[A-Z]  number of scans in average for this band name
```

This table may be calculated by the HOPS software package `snratio` command.

* The first column lists baselines as pairs of 1-character HOPS station IDs
  * The order of stations in the baseline should be the same order that HOPS used
  * The order of rows is arbitrary
* The even columns (2nd, 4th, and so on) list the average SNR ratio for each baseline for a particular band over all sources
  * The header name is the 1-character band name
  * Several filters are used during this calculation:
    * Observed SNR for scans with an `F`, `E`, or `B` error code are ignored
    * Observed SNR for scans with a `0` quality code are ignored
    * Observed SNR falling outside the range 6.9 < SNR < 5000.0 are ignored
    * SNR ratios greater than 5.0 are ignored
* The odd columns after the first (3rd, 5th, and so on) list the number of scans included in the average
  * The column header for each is the same as the previous column but with an `n_` suffix

## `+EOP` Section

This section lists the earth orientation parameter (EOP) model values used during correlation. These values may also be present with a different format in the `+CORRELATOR_CONFIG_FILE` section, or in the VEX file.

```
+EOP

  mjd  tai-utc     ut1-utc     xpole     ypole
----------------------------------------------
58464     37.0  -0.0204192  0.126826  0.270904
58465     37.0  -0.0209216  0.124899  0.270498
58466     37.0  -0.0215037  0.123057  0.270012
58467     37.0  -0.0221965  0.121150  0.269523
58468     37.0  -0.0230079  0.119267  0.268982

* mjd      integer modified Julian date
* tai-utc  (sec) TAI minus UTC offset
* ut1-utc  (sec) UT1 minus UTC offset
* xpole    X pole EOP parameter
* ypole    Y pole EOP parameter
```


## `+CORRELATION` Section

This **dictionary section** lists the correlation software and settings used. While the key names used are borrowed from DiFX terminology, most if not all correlation software algorithms are likely to have an analogous set of settings. This section is especially useful when replicating a correlation using a different software or software version than was used for the original correlation. Note that you may add additional settings to this section as needed. When adding new settings, please choose clear, concise, and specific names.

```
+CORRELATION

SOFTWARE    DiFX
VERSION     2.6.1
PATCH       difx2mark4 bugfix
ALGORITHM   FX
NCHAN       16
FFTSPECRES  0.125 MHz
SPECRES     0.5 MHz
TINT        1.0 sec
```

Key          | Type         | Description
-------------|--------------|------------
`SOFTWARE`   | string       | correlation software name
`VERSION`    | string       | correlation software version; append release number or commit hash with a dash if applicable (e.g. `3.4.5-42e6dc3`)
`PATCH`      | string       | list of descriptions of patches applied by the local correlator, but not in the official release of the software
`ALGORITHM`  | string       | general algorithm used by software, `FX` or `XF`
`NCHAN`      | integer      | number of channels per spectral window
`FFTSPECRES` | number (MHz) | spectral resolution of first stage FFTs
`SPECRES`    | number (MHz) | spectral resolution of visibilities
`TINT`       | number (sec) | time of integration (accumulation period)

## `+FRINGING` Section

This **dictionary section** lists the fringing software (and optionally extra settings) used. This section may be especially useful when replicating a fringing using a different software or software version than was used for the original fringing. Note that you may add additional settings to this section as needed. When adding new settings, please choose clear, concise, and specific names.

```
+FRINGING

SOFTWARE  HOPS
VERSION   3.22-3226
PATCH     VGOS station listing bugfix
```

Key        | Type   | Description
-----------|--------|------------
`SOFTWARE` | string | fringing software name
`VERSION`  | string | fringing software version; append release number or commit hash with a dash if applicable (e.g. `3.4.5-42e6dc3`)
`PATCH`    | string | list of descriptions of patches applied by the local correlator, but not in the official release of the software

## `+VGOSDB` Section

This **dictionary section** lists the VGOSDB making software (and optionally extra settings) used. Note that you may add additional settings to this section as needed. When adding new settings, please choose clear, concise, and specific names.

```
+VGOSDB

SOFTWARE  nuSolve
VERSION   0.7.2
PATCH     Qt RGBA version compatibility fix
```

Key        | Type   | Description
-----------|--------|------------
`SOFTWARE` | string | VGOSDB creation software name, or name of parent application
`VERSION`  | string | VGOSDB creation software version; append release number or commit hash with a dash if applicable (e.g. `3.4.5-42e6dc3`); if `SOFTWARE` lists the parent application name, then give the version number of the parent application
`PATCH`    | string | list of descriptions of patches applied by the local correlator, but not in the official release of the software

## `+CORRELATION_CONFIG_FILE` Section

This **text** section contains the content of the correlation configuration file. If the correlation software does not have a separate configuration file, then either omit this file, or include a line with the program name and arguments used to invoke correlation, e.g. `my-corr-prog --nchan 64 --fftres 0.125m`. For a DiFX correlation, this should be the contents of the VEX to DiFX (V2D) file.

```
+CORRELATION_CONFIG_FILE

vex = i22101.vex
singleScan = true
antennas = KK, WZ

SETUP normalSetup {
  FFTSpecRes = 0.03125
  specRes = 0.125
  tInt = 2
}

RULE scansubset {
  setup = normalSetup
}

ANTENNA KK {
  phaseCalInt = 1
  toneSelection = all
  filelist = i22101_kk.filelist
}

ANTENNA WZ {
  phaseCalInt = 1
  toneSelection = all
  filelist = i22101_wz.filelist
}
```

Notes:

* You may (but don't have to) omit extraneous comments
* You may (but don't have to) omit EOPs, since they are also included in the `+EOP` section
* Be very careful to pre-pend comment markings to wrapped comment lines if you choose to hard-wrap this section to a particular character width

## `FRINGING_CONFIG_FILE` Section

This **text** section contains the content of the fringing configuration file. If the fringing software does not have a separate configuration file, then either omit this file, or include a line with the program name and arguments used to invoke correlation, e.g. `my-fringe-prog --all-baselines`. For a HOPS fringing, this should be the contents of the HOPS configuration (CF) file.

```
+FRINGING_CONFIG_FILE

sb_win -256.0 256.0
mb_win -256.0 256.0
dr_win -0.03 0.03
pc_mode multitone
pc_period 5

if f_group S
  ref_freq 2225.99

if f_group X
  ref_freq 8212.99
```

Notes:

* You may (but don't have to) omit extraneous comments
* Be very careful to pre-pend comment markings to wrapped comment lines if you choose to hard-wrap this section to a particular character width
* HOPS `fourfit` argument `set gen_cf_record true` will store this CF file in the fringe file's `204` record

## `+END` Section

```
+END
```

`+END` is a blank section with no content. It marks the end of the report. The last character of the report should be the newline terminating the `+END` line. Parsers may choose either to parse this section as an empty dictionary section or not include it in the parsed results at all.
