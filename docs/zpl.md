# ZPL: Zebra label printers and compatibles

How Tavletta talks to printers that speak ZPL (Zebra Programming Language):
Zebra's desktop and industrial printers (GK, GX, ZD, ZT series and others)
and the many printers that emulate ZPL. Tried on a Zebra GX420d (203 dpi,
firmware V56.17.17Z) over USB: the status questions and printing; other
models and the network connection are not tried yet.

The byte sequences follow [LPrint](https://github.com/michaelrsweet/lprint)
by Michael R Sweet (Apache-2.0, `lprint-zpl.c`), which prints on these
printers; Tavletta reimplements them in Python, no LPrint code is included.
Zebra's "ZPL II Programming Guide" describes the commands.

Markers: **[LPrint]** as LPrint sends it, **[spec]** from Zebra's manual,
**[assumed]** likely but untested, **[open]** unknown.

## Transport

- TCP port 9100, or USB through the kernel's printer driver
  (`/dev/usb/lpN`); the same commands on both. [spec]
- Found on USB by the IEEE 1284 device ID: a command set (`CMD:`) with
  `ZPL`, or a maker starting with "Zebra" when the ID names no command set.
  Examples from LPrint: `MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD621-203dpi ZPL;`.
  [LPrint]
- Found on the network by mDNS `_pdl-datastream._tcp`: `usb_CMD` in the TXT
  record names ZPL, or the service or `usb_MFG` starts with "Zebra".
  [assumed]
- Questions are answered on the same connection; Tavletta waits 1 s for
  each and treats silence as "no status" (state UNKNOWN), not as an error.
  [assumed]
- An answer that comes after its 1 s ends up in front of the next answer:
  the frames are told apart by their number of fields (`~HI` 5, `~HS` 12,
  11 and 2), not by their place.
- Sending: the timeout of 10 s counts from the last bytes the printer took,
  so a large job on a slow printer goes through; a job may take 10 s plus
  its size at 8 kB/s at most. A connection reset while sending is a
  printer that stopped taking data, not a missing printer.
- After the job Tavletta closes its sending side and reads and drops what
  the printer still says (closing with an unread answer would reset the
  connection and cut the job off). It is done once the printer has
  acknowledged every byte and then closed its side, stayed quiet for 1 s,
  or kept talking for 4 s more. A printer that takes nothing for 10 s, or
  resets or closes the connection before it has everything, is an error.
  A printer that resets the connection right after it took the complete
  job is reported as stalled too: TCP cannot tell that reset from one in
  the middle of the job (bytes acknowledged by the kernel need not have
  reached the printer's interpreter). [assumed: rare]

## Status and identification

| Request | Answer | Used for |
|---|---|---|
| `~HI` | `<STX>ZD621-203dpi,V93.21.18Z,8,8192KB<ETX><CR><LF>` | model, firmware, dots per mm (8, 12, 24 = 203, 300, 600 dpi) |
| `~HS` | three frames `<STX>…<ETX><CR><LF>` | paper out, paused, labels waiting (frame 1); head up, ribbon out, labels remaining (frame 2) |
| `~HQES` | text with `ERRORS: 1 00000000 00000005` | the second hex word is a bit field of errors |
| `^XA^HH^XZ` | one frame of lines `  <value>   <NAME>` | the loaded media: LABEL LENGTH, PRINT WIDTH, MEDIA TYPE, RESOLUTION (head dots), PRINT METHOD |

- `~HI`: LPrint reads the model and dots per mm the same way. [LPrint]
- `~HS` frame 1 is `aaa,b,c,dddd,eee,…`: b paper out, c paused, dddd label
  length in dots, eee formats in the buffer. Frame 2 is
  `mmm,n,o,p,q,r,s,t,uuuuuuuu,…`: o head up, p ribbon out, q thermal
  transfer mode, uuuuuuuu labels remaining. LPrint reads only the label
  length. [spec] Seen on a GX420d: `030,0,1,0422,…` (paused, 422 dots)
  and `000,0,0,0,0,2,4,0,00000000,1,000` (direct thermal, tear off).
- `~HQES` error bits (from LPrint's header): `0x1` media out, `0x2`
  ribbon out, `0x4` head open, `0x8` cutter fault, `0x1000` paper jam,
  `0x10000` paused, `0x40000`/`0x80000` black mark not found. Tavletta
  asks it only when `~HS` was answered and showed no problem. [LPrint]
- `^HH` sends the configuration to the host and prints nothing (seen on a
  GX420d, firmware V56.17.17Z, both while paused and while idle: it
  answered and fed no label; its answers are the fixtures in
  `tests/zebra_gx420d.py`). A status query asks it once `~HS` was
  answered, not while the printer is printing (`~HS` shows labels or
  formats waiting). The answer counts once a frame with LABEL LENGTH or
  RESOLUTION has come, not at the first frame: a late `~HQES` answer may
  come first. A printer that leaves `^HH` unanswered is not asked again
  for 10 minutes (each question waits the whole answer timeout). The
  answer (about 3.5 kB) is read while it keeps coming, up to 10 s: on a
  serial adapter at 9600 baud it takes about 4 s. Part of it (the line
  went silent) still counts as answered, and what came is used for a
  minute without asking again (each try could take the 10 s), with the
  print method `~HS` reports each time: a label distance changed on the
  printer meanwhile then shows up to 60 s late, unless Tavletta printed a
  job on it (that forgets what came, the printer may have measured the
  labels anew); a whole frame of settings whose names Tavletta does not
  know counts as answered too (see below).
- Each line of `^HH` is `  <value>   <NAME>`: the name is what follows
  the last run of two or more spaces (`39.0IN   989MM      MAXIMUM
  LENGTH`). The names are English only while the printer's display
  language is English; in another language Tavletta finds none of them
  and the status shows no label distance (the rest of the status works).
- On gap or mark media LABEL LENGTH is what the printer measured from one
  label to the next: the label distance, compared with the label's size
  plus gap, as for cab (within 1 mm, or 1 % of a long label). The
  conversion uses the dots per mm of RESOLUTION (`832 8/MM FULL`; 300 dpi
  printers report 12/MM, which Zebra's manuals define as 300 dpi, 600 dpi
  ones 24/MM), else those of `~HI`. LABEL LENGTH is the last measurement:
  with MEDIA POWER UP NO MOTION and HEAD CLOSE FEED (the GX420d's setup)
  the printer does not measure again after a roll change, so the hint
  about another label distance suggests calibrating the printer too. A
  Tavletta job's `^LL` does not change it: after a job with `^LL400` on
  gap labels the GX420d still reported LABEL LENGTH 0422, its measured
  distance. MEDIA TYPE is GAP/NOTCH, MARK or CONTINUOUS; older firmware
  says NON-CONTINUOUS and names the sensor in SENSOR TYPE (WEB = gap,
  MARK = black mark; TRANSMISSIVE and REFLECTIVE are read as gap and mark
  too), else in SENSOR SELECT. Only WEB was seen on a printer; the other
  spellings are assumed, not verified on a device. The comparison is skipped when the job's `media`
  setting is neither `printer` nor the type the printer reports (a
  continuous job on a printer set to gap labels).
- PRINT WIDTH is a setting, not the loaded labels: each Tavletta job sets
  `^PW` to the label's width, and after a power cycle the GX420d reported
  the whole head again (832). The GX and ZD series centre narrower labels
  under the head, and they centre the `^PW` area with them: a label of the
  right size prints centred (seen on the GX420d with 75 mm labels). So
  Tavletta only shows it (printer details, the API's `width_mm`) and
  warns about nothing; "Use loaded labels" in the window keeps the
  label's width and fits only its height to LABEL LENGTH
  (`job.fit_loaded()`). The print method is `~HS` frame 2 field q (1
  thermal transfer), or PRINT METHOD in `^HH`. SGD (`! U1 getvar`) would
  give the same values on printers that have it; not used yet.
- Before a job Tavletta asks `~HS` (and `~HQES`) and stops if the printer
  reports a problem; a printer that does not answer gets the job anyway.
- Answers that do not have this shape (garbage, half an answer) count as no
  answer. The resolution "As reported by the printer" uses `~HI`; without an
  answer: with "As reported by the printer" each print asks `~HI` again;
  resolutions are kept per printer address, and the label is rendered for
  the one of the printer it is printed on. If the printer now reports
  another resolution, a whole ratio upwards (300 to 600 dpi) is scaled dot
  by dot, any other (600 to 300 or 203 to 300 dpi) renders the label again. A printer not heard
  of before gets 3 s for `~HI` instead of 1 s; one that never answered it
  gets no job, and Tavletta asks to choose the resolution instead of
  guessing.

## A print job

```
~SD15                                (darkness, only if set: 0 to 30)
~DGR:TAVLETTA.GRF,6,2,IF0:,          (the graphic: total bytes, bytes per row, data)
^XA
^MMT,Y                               (after printing, only if set: T tear, P peel, C cut)
^PR4,4,4                             (speed in inches per second, only if set)
^PON                                 (not turned: the first row leaves first)
^PW16                                (print width in dots)
^LH0,0
^LL3                                 (label length in dots)
^MNY                                 (sensor, only if set: Y gap, M black mark, N continuous)
^PQ1,0,1,N                           (number of labels)
^FO0,0^XGR:TAVLETTA.GRF,1,1^FS       (place the graphic)
^XZ
^XA^IDR:TAVLETTA.GRF^FS^XZ           (delete it again)
```

- The label is downloaded to the RAM drive (`R:`) with `~DG` and printed with
  `^XG`, not with `^GFA` or `:Z64:`. Rows are hex, 1 = black, the leftmost
  dot in the highest bit, padded with white to whole bytes. [LPrint]
- Compression: a run of a hex character is written as `z` per 400, one of
  `g`…`y` for 20 to 380, one of `G`…`Y` for 1 to 19, then the character;
  a row equal to the previous one is `:`; trailing zeros are `,` (an odd
  number of zeros leaves one `0` first). Example, 16 dots, rows `FF F0`,
  `FF F0`, `00 00`: `~DGR:LPRINT.GRF,6,2,IF0:,`. Tavletta's tests check
  this example byte for byte and decode random rows again. [LPrint] [spec]
- `^PON`: the first row of the image leaves the printer first, as in the
  other raw languages. LPrint sends `^POI` (turned by 180°). Which way
  round the label comes out has not been seen on a printer; "Print upside
  down" turns the image by 180° for a printer where it comes out the wrong
  way. [spec] [open]
- `^MN`: LPrint sends `^MNM` (black mark) for gap labels too; by the manual
  gap labels are `^MNY` ("web"), and Tavletta sends that. By default
  ("As set on the printer") it sends no `^MN` at all. [spec]
- `^LL`: LPrint sends the length only for continuous media; Tavletta sends
  it always, so the first label is right before the printer has calibrated.
  [assumed]
- `^PQ` with the number of copies in one job; LPrint prints one label per
  job. [spec]
- Darkness: the setting in per cent, `~SD` is 30 × per cent / 100. `~SD`,
  `^PR` and `^MM` are sent only when set in Tavletta; otherwise the
  printer's setup applies. [LPrint] [spec]
- Tavletta does not track the job: it is done once it is sent. [assumed]

## Settings in Tavletta

| Setting | Values | Command |
|---|---|---|
| Resolution | As reported by the printer, 203, 300, 600 dpi | renders at 8, 12 or 24 dots per mm |
| Label sensor | as set on the printer, gap, black mark, continuous | none, `^MNY`, `^MNM`, `^MNN` |
| After printing | as set on the printer, tear off, peel off, cut | none, `^MMT,Y`, `^MMP,N`, `^MMC,Y` |
| Darkness | as set on the printer, 0 to 100 % | `~SD00` to `~SD30` |
| Speed | as set on the printer, 1 to 12 in/s | `^PR` |
| Print upside down | off, on | the image turned by 180° |

Widest label: 104 mm, the 832 dots of a 4 inch head at 203 dpi. [spec]

## Not used

- `:Z64:` (zlib and base 64), shorter than hex for large labels.
- `^MT` (thermal transfer or direct), `~TA` (tear-off offset), `^LT`
  (top offset): the printer's setup stays in charge.
- Printer fonts and barcodes: Tavletta sends the rendered label as one
  graphic, so the preview stays exact.
