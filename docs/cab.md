# cab printers: JScript over the network

How Tavletta talks to cab label printers with a 4 inch print head (EOS2,
EOS5, SQUIX 4, MACH 4S). JScript is cab's own printer language and is
publicly documented by cab: "JScript Programming Manual" for SQUIX, MACH 4S,
EOS2, EOS5, XC Q, XD Q, HERMES Q, PX Q, AXON 1/2, linked from
<https://www.cab.de/en/programming>. Nothing here is reverse engineered;
the device answers below were seen on an EOS2/300 with firmware
5.47.7 (Jan 22, 2026).

Sources quoted as **[manual]**:

- "JScript Programming Manual", edition 07/2026, firmware 5.48
  (<https://www.cab.de/media/pushfile.cfm?file=3047>): ESC commands
  (2.x), `q` queries (3.x), `H` (4.8), `O` (4.12), `S` (4.15), `A [PREVIEW]`
  (4.1).
- "Programming a cab Printer" (JScript guide), edition 04/2022
  (<https://www.cab.de/media/pushfile.cfm?file=3963>): 4.2.4 "Speed and
  heat", 9.4 "Ribbonsaver", tables 14.2 (`H`) and 14.3 (`O`).

Markers: **[manual]** from cab's manual, **[confirmed]** seen on the device,
**[assumed]** likely but untested, **[open]** unknown.

## Transport

- TCP port 9100 ("RawIP"), with the interpreter for RawIP set to JScript
  (the default; the printer can also run ZPL there). [confirmed]
- USB 2.0 (device port): Linux's printer class driver (usblp) makes it
  `/dev/usb/lpN`, which takes the same JScript and answers the ESC
  commands, as the driver is bidirectional. Tavletta finds cab printers
  there by the IEEE 1284 device ID in sysfs (`MFG:cab`) and addresses them
  like CUPS: `usb://cab/EOS2%2F300?serial=...`. [manual] [assumed: not yet
  tried on a printer]
- Timeouts on USB: 10 s for an answer; while sending a job, 10 s without
  the printer taking any data (each write that gets data through starts it
  again), at most 10 s + the job's size at 8 kB/s (`MIN_RATE`). A print
  waits up to 45 s for a status query still using the device, a status
  query 5 s. On TCP the same: 10 s without progress, at most 10 s + the
  job's size at 8 kB/s.
- Commands end with CR; Tavletta sends CR LF. [confirmed]
- ESC commands (`0x1B` + letter) are executed at once, also while a job is
  running, and answer on the same connection without a terminator unless
  stated. [manual] [confirmed]
- Status requests not more often than every 0.5 s. [manual]
- The printer announces itself by mDNS as `cab <model> (<MAC suffix>)`, e.g.
  `cab EOS2/300 (aa:bb:cc)`, with `_pdl-datastream._tcp` on port 9100,
  `_printer._tcp`, `_http._tcp` and `_ftp._tcp`; the TXT record has
  `usb_MFG=cab`, `usb_MDL=EOS2/300` and `pld=application/vnd.cab-jscript`.
  [confirmed]

## Status

| Request | Answer | Meaning |
|---|---|---|
| `ESC s` | `Y-000000N` (9 characters) | online (`Y`/`N`), error (`-` = none), labels still to print (6 digits), interpreter active (`Y`/`N`) |
| `ESC z` | `NNNNNNNNNNNNN` CR | paused, has a job, not ready for data, paper moving, ribbon warning, paper end warning, … (13 × `Y`/`N`) |
| `ESC l` | `Y0020` | labels synchronised (`Y`/`N`), measured distance from label to label in mm (`0000` = unknown) |
| `qv` CR | `5.47.7 Jan 22, 2026 (EOS2/300)` CR | firmware and model; the number after `/` is the resolution in dpi |
| `qp` CR | `NONE` CR | peripherals: `CUTTER`, `REWINDER`, … |

[manual] [confirmed]

- An answer has 10 s in all (not 10 s for each byte), and one up to CR
  at most 256 bytes: something else on port 9100 that talks without end
  does not hold the connection. [decided]
- Error characters of `ESC s` that Tavletta names: `P` out of paper,
  `D` print head or pinch roller open, `E` synchronisation error (no label
  found), `F` out of ribbon, `R` ribbon in thermal direct mode (see "Print
  method"). Others (`B`
  protocol error, `H` heating voltage, `O` out of memory, `W` head
  overheated, `Y`/`Z` print head error or damaged, …) are reported with the
  raw answer. [manual]
- After a text command such as `qv` the printer reports "interpreter
  active" (`Y-000000Y`) with no labels pending for about one second.
  ESC commands alone do not. Tavletta therefore waits for `N` before it
  sends a job. [confirmed]
- `ESC l` reports the distance of the loaded labels (label height + gap)
  once the printer has synchronised, e.g. after Feed. [manual] [confirmed]
  [open: a test printer answered `Y0020` with a roll that cablabel describes as
  18.6 mm high with a 22.6 mm distance (see "What cablabel sends"); the
  window would then warn "Different labels loaded" for the right label.
  To check after pressing Feed whether the printer measures 22 or 23 mm.]
- Widest label: 105.6 mm, the print width cab gives for 4 inch heads.
  `S l1;0,0,25,28,106` is refused with "Syntax error" on the display (and
  `B` in `ESC s`) although the head reports "106 mm"; 105.58 mm is
  accepted. [confirmed]
- The print head reports itself in the web interface as "106 mm 12 dots/mm"
  on the EOS2/300; Tavletta renders at exactly 12 dots per mm (8 for /200,
  23.6 for /600 models), so a 100 mm label is 1200 dots. A print asks
  `qv` first, unless the printer answered it in the last 30 s
  (`VERSION_SECONDS`): `qv` keeps the interpreter active for about a
  second, which each label of a series would wait for. [confirmed for the
  /300] [decided]
- An image rendered for another resolution than `qv` reports is scaled
  dot by dot if the ratio is a whole one upwards, else the label is
  rendered again for the printer's resolution (as for ZPL: scaling down
  or by 1.5 spoils bars and thin lines). [decided]
- Before `qv` each print asks `ESC s`: a printer with a problem may not
  answer text commands, its status tells the problem (out of labels, head
  open) instead of a timeout. [assumed]

## A print job

```
d PNG;TAVLETTA
<BASE64>
iVBORw0KGgo…              (the label as 1-bit PNG, one line of base 64)
</BASE64>
m m                       (millimetres)
J                         (job start)
H 50,0,T                  (only with a print method chosen in Tavletta, see below)
S l1;0,0,25,28,50         (gap sensor; 0,0 offset; height 25, height + gap 28, width 50)
O R                       (only with "Print upside down": the label turned by 180°)
I:Label;0,0,0;TAVLETTA    (place the image at the top left, not rotated)
A 1                       (number of labels)
```

- `d PNG;name` with a base 64 block downloads an image into RAM; a name has
  at most 8 characters and a new download replaces the previous image of the
  same name. [manual]
- The image is the whole label at one pixel per dot: x across the print
  head, y along the feed. Tavletta turns it for content
  along the roll and dithers photos for the mono print head. [assumed:
  one pixel per dot, to be confirmed on a printout]
- `S l1;…` selects die-cut labels with gap detection for this job; `S e`
  would be continuous media, `S l2` reflective marks. [manual]
- `H` only if a print method is chosen (next section). `O R` only with
  the setting "Print upside down" (`upside_down`, off by default), directly
  after `S` as the manual asks; no other `O` options (tear-off, backfeed):
  the printer's setup stays in charge of those. [manual]
- "Print offset" (`offset_mm`, −5 to 5 mm, 0 by default) moves the
  image's content along y before it is sent, by whole dots: positive
  down (towards the image's last rows), the rows it leaves white, what
  moves out cut off. `S` keeps offset 0,0, the label's size and the
  image's place stay. Only the job moves: the preview, the editor's
  boxes and the server's preview keep showing the label; the server's
  `"view": "printer"` is the image as sent, moved. [decided]
- Images are not erased before the download: Tavletta always uses the one
  name `TAVLETTA` (a series' jobs: `TAV00001` onwards, see below), which
  each download replaces, so the RAM does not fill up, and images other
  programs left in the printer stay. [manual]
- `A [PREVIEW]` instead of `A 1` renders the label without printing; the
  result is shown at `http://<printer>/cgi-bin/bitmap` (behind the web
  interface's password). The job above was accepted that way without a
  protocol error. [confirmed]
- While a job runs, the label counter of `ESC s` counts down to 0 and the
  last character goes back to `N`. [manual] Tavletta counts a job as done
  once it saw labels pending and then none (the interpreter may still be
  active: the second after a job, a tear-off feed); a job whose labels
  were never seen pending (a short label printed between two polls, which
  are at least 0.5 s apart) once the printer, seen active after the job
  was sent, is idle at two polls in a row; a job not seen at all after
  3 s without any activity. [decided] [open: how fast it counts on a real
  printout]
- Label after label (a series, or copies with variables): the next job
  is sent as soon as the one before counted down to 0, also while its
  interpreter is still active (within 2 s, `TAIL_SECONDS`); otherwise a
  job waits until the printer is idle, as another program's job may be
  running. Each label is still confirmed before the next is sent, so the
  labels printed are known exactly when one fails. With a timed fake
  printer (0.2 s to read the image, 0.4 s to print a 20 mm label, the
  interpreter active 1 s after) a label took 3.0 s, 5.5 s when the print
  fell between two polls; now 1.0 to 2.3 s. Sending the labels of a
  series without waiting (the printer queues them) would reach the
  printer's speed, but then a failing label could not be told from those
  queued behind it. [measured with the fake printer] [open: on a real
  printout]
- On an error while sending or during a job (out of labels, head open,
  timeout) Tavletta sends `ESC c` (cancel job), so that the remaining
  labels are not printed once the problem is solved and the user prints
  again. [manual] After a job cut off while sending, it should also drop
  the half-downloaded image, which on USB would otherwise run into the next
  job. [assumed: not tried on a printer; a printer that stalled may not
  take `ESC c` at all, so Tavletta tries it for 1 s only and then closes]
- Peel-off and print on demand (the printer waits for a button or a removed
  label) are not supported: the job would time out after 180 s.

## Print method (thermal transfer or thermal direct)

The printer prints with a ribbon (thermal transfer) or without (thermal
direct), as set in its setup (Setup > Ribbon > Transfer print, `On`/`Off`).
A thermal transfer label printed in thermal direct mode comes out blank or
smeared, and the ribbon is not pulled. Seen on an EOS2/300 whose setup said
"Transfer print: Off" while a ribbon and thermal transfer labels were
loaded: the counters of the web interface showed 52.23 m thermal transfer
and 0.156 m thermal direct, the Windows driver printed fine (it sends the
method with every job), Tavletta's job did not pull the ribbon.
[confirmed]

### The `H` command [manual]

`H speed[,h[:h]][,P[x]][,t[:t]][,r][,s][,Bb]` (manual 4.8), sets for the
current job:

| Field | Meaning |
|---|---|
| `speed` | print speed in mm/s (inch/s with `m i`); **required**. A value the printer does not have is rounded to the next one it has, a value above its maximum becomes the maximum. |
| `h` | heat, -20 to +20; added to the heat level set on the printer ("the value for the heat is added to the default value in the printer", guide 4.2.4 and 9.4), so `0` keeps it. |
| `t` | `T` thermal transfer (with ribbon), `D` thermal direct (without); not given: the setup's value (guide table 14.2). `TI`/`TO` (ribbon ink side) are not available on EOS2/5 and MACH 4S. |
| `r` | `R0`/`R1` ribbon saver, only with that option fitted. |
| `Bb` | backfeed speed; 100 mm/s if not given. |
| `P[x]`, `:h`, `:t`, `s` | paper saving and the second head: X4 printers with two heads (XC Q, XD Q) only. |

Examples in the manual: `H 100,0,T`, `H 150,-5,T`, `H 150,0,D,R1,B75`,
`H 100,S1`; `H` comes after `J` and before `S`.

### What Tavletta sends

Setting "Print method" (`method` in settings.json, per printer model):

| Choice | Value | Job |
|---|---|---|
| As set on the printer (default) | `printer` | no `H` line: as before |
| Thermal transfer (ribbon) | `transfer` | `H <speed>,0,T` |
| Thermal direct | `direct` | `H <speed>,0,D` |

- Speed: `H` cannot leave it out. Tavletta sends the speed set on the
  printer, read from the web interface's setup
  (`GET /cgi-bin/get?cmd=1`, entry `ID_PRINT_SPEED`, e.g. `"50 mm/s"`).
  The setup is kept for 30 s per printer (a series of labels, each a job
  of its own, asks once); asking for the printer's state reads it anew,
  so a speed changed on the printer counts from then on, or after 30 s.
  If the web interface does not answer within 2 s
  (switched off, behind a password, USB) or shows inch/s, it sends 50 mm/s,
  the default of cab's setup and the slowest common speed (slower prints
  cleaner, guide 4.2.4). Trade-off: then a printer set to a faster
  speed prints slower than set, one set to 30 or 40 mm/s faster. [assumed:
  50 mm/s as cab's factory default, from the configuration manuals of
  other cab models; the EOS2's own manual gives no number]
- Heat: `0`, which adds nothing to the printer's heat level.
- Default "As set on the printer": a printer used for thermal direct
  labels without a ribbon keeps working as before; choosing thermal
  transfer by default would stop it with "out of ribbon" (`F`), and the
  speed would always be sent. The choice is kept per printer model, so it
  is made once.
- `--print-method printer|transfer|direct` on the command line overrides
  the app's choice for one print, also together with `--printer-defaults`
  (whose default is `printer`).
- Accepted by an EOS2/300 (firmware 5.47.7) with `A [PREVIEW]`:
  `H 50,0,T` and `H 50,0,D` before `S l1;0,0,17,20,50`, `ESC s` stayed
  `Y-000000N`/`Y-000000Y` (no `B`), no label was moved. [confirmed]
  Whether the ribbon is really pulled with `T` and the setup on `Off`:
  [open: to be confirmed on a printout]

### Knowing the printer's method

- JScript has no query for the setup's print method: `q` (manual 3.x)
  reports memory, peripherals (`qp`), ribbon diameter (`qr`, `-1` if not
  measured), label roll diameter (`qw`) and firmware (`qv`); `qo`
  (statistics with `mm_transfer`/`mm_thermo` counters) is for X4 printers.
  [manual] [open: what `qr` answers on the EOS2 in thermal direct mode
  with a ribbon loaded; not asked yet]
- The web interface's setup tells it without a password on firmware 5.47:
  `ID_TRANSFER_PRINT`, a radio button with `"options": ["Off", "On"]`,
  `"values": ["false", "true"]` and the shown `"selection"`. Tavletta reads
  the value at the position of the selection (the texts follow the
  printer's language) and shows it in the printer details as "Print method
  set on the printer". Only a hint: the web interface may be off or behind
  a password, and the media loaded is not known. [confirmed]
- `/cgi-bin/get?cmd=3` (status) has the counters `thermalTransfer` and
  `thermalDirect` in metres, not used, and `"labels": [2686, "-"]`, the
  labels printed in the printer's life, with `serialno` and
  `printerModel`; without a password on firmware 5.47. Tavletta reads the
  label counter for what a job printed (see "What a job printed").
  [confirmed]
- `ESC s` error `R` "Ribbon detected in thermal direct mode" and `F` "Out
  of ribbon" [manual]: Tavletta reports `F` as "the printer finds no
  ribbon" (load one, or choose thermal direct) and `R` as "a ribbon is
  loaded, but the printer prints without it" (choose thermal transfer, or
  remove the ribbon), during a print and in the status. On the EOS2/300
  with a ribbon loaded and "Transfer print: Off", `ESC s` said
  `Y-000000N` at rest, and during the print described above Tavletta's
  status polls reported no error either. [confirmed] So `R` cannot be
  relied on for this mismatch; the printer seems not to notice a ribbon it
  does not drive. [assumed] `ESC z` position E "ribbon warning" is
  hardware dependent. [manual]

## What cablabel sends

cablabel S3 Lite (driver 1.0.7.3, target EOS2/300) sent this for a
40 × 18.6 mm label with a 4 mm gap on a test printer (images and job, captured
on the way to the printer): [confirmed]

```
e IMG;*
d IMG;63125458           (three times: text as images, cab's IMG format between ESC . … ESC .)
l SG
m m
zO
J Example Clinic
j 3177983793822070
S l1;0,0,18.6,22.6,40;Example Clinic
H 50,10,T,R0,B0
O R,P
T:Counter1;0,0,0,3,3;[SER:100001][WINF][I]
I:Text1;2.57,14.69,0,1,1,a;63125458
B:Barcode1;23.49,3.75,0,qrcode+ELL+MODEL2,0.67;[Counter1]
I:Text2;2.5,3.43,0,1,1,a;43923569
I:Text3;2.54,8.23,0,1,1,a;13794620
A 1
```

| Line | Meaning [manual] | Tavletta |
|---|---|---|
| `e IMG;*` | erase all images of type IMG from RAM (3.4; `*` is a wildcard) | not sent: one image name, replaced each time (a series: `TAV00001` onwards, used again by each job) |
| `d IMG;name` + `ESC .` … `ESC .` | download in GEM IMG format, binary between `ESC .` (3.3, 2.3) | `d PNG;TAVLETTA` in base 64 (3rd method of 3.3) |
| `l SG` | locale Switzerland/German: date and number formats of printer-side fields (3.8) | not needed: no printer-side fields |
| `zO` | unslashed zero, bitmap fonts only (3.17) | not needed |
| `J Example Clinic` | job start; the comment is obsolete from X3 on (4.10) | `J` |
| `j <id>` | job ID, read back with `ESC j` (3.7) | not sent |
| `S …;Example Clinic` | label size; the text after `;` is shown on the display (4.15) | without name |
| `H 50,10,T,R0,B0` | 50 mm/s, heat +10, thermal transfer, ribbon saver off, backfeed speed 0 (4.8) | `H <speed>,0,T` with a print method chosen |
| `O R,P` | label turned by 180°; backfeed "smart", i.e. suppressed for this job (4.12) | `O R` with "Print upside down"; no `P` |
| `T`, `B`, `I` | counter, QR code and texts as printer objects | the whole label as one image |

- `O R` is why cablabel's labels come out the right way round for the
  user; Tavletta offers it as "Print upside down" per printer, off by
  default (a label already printed the other way stays so). The setting
  and its translations already exist for the ZPL, TSPL, EPL2, ESC/POS and
  DYMO printers. Accepted with `A [PREVIEW]`:
  `H 50,0,T` / `S l1;0,0,18.58,22.58,40` / `O R` (no `B`). [confirmed]
- Heat: cablabel adds +10 to the printer's heat level for this material;
  Tavletta adds 0. If Tavletta's thermal transfer prints come out pale,
  raise the heat level in the printer's setup (cablabel would then add its
  +10 on top). [open: compare on a printout]
- `B0`: backfeed speed 0 mm/s; together with `O P` (no backfeed) probably
  meaningless. The manual only says 100 mm/s is used if `B` is missing.
  [open]
- `O P` keeps the label in place instead of pulling it back after the
  tear-off feed; the manual warns of a thin white line where the label
  stood under the head. Tavletta leaves this to the setup ("Backfeed").

## A series as jobs of several labels

Each label of a series as a job of its own (`J` … `A 1`) makes the
printer feed the label to the tear-off edge at the end of each job and
pull it back at the start of the next: the labels do not run through.
[confirmed on a printout] Copies of one label (`A n`) run through. So a
series without variables that change from copy to copy goes as jobs of
up to "Labels per job" labels (`labels_per_job`, 1 to 1000, 250 by
default; 1 prints label by label), in one transmission each:

```
d PNG;TAV00001   <BASE64> … </BASE64>      (every label's image first)
d PNG;TAV00002   …
m m
J
H 50,0,T
S l1;0,0,18.6,20,40
O R
I:Label;0,0,0;TAV00001
A 1
R Label;TAV00002         (the next label's image into the same field)
A 1
…
```

- `R name;data` replaces a field's data and is followed by another `A`
  in the same job: the manual shows it for a text field (`T:REP;…` /
  `A1` / `R REP;cab printers` / `A2` / …) and names text and barcode
  fields. [manual] It puts another image into an image field too: 5
  labels of an inventory series came out with 5 different numbers, run
  through in 3.2 s, centred with "Print offset" 1.25, the gap sensor
  (`S l1`) synchronising on every label. [confirmed on an EOS2/300,
  firmware 5.47.7, 2026-11-24]
- The images are named `TAV00001` onwards (8 characters), each job using
  the names again: the RAM holds at most "Labels per job" of them,
  besides `TAVLETTA`. They stay after the job, as `TAVLETTA` does.
  [decided] An inventory label (40 × 18.6 mm) is a PNG of about 1.4 KB,
  2 KB in base 64: 250 labels take about 0.5 MB. [measured] A printer
  that runs out of memory reports `O` in `ESC s`: Tavletta cancels the
  job and says to lower "Labels per job" (`printer_out_of_memory`).
- Tavletta holds a job's labels as PNG until it is sent, each packed as
  soon as it is rendered (`PackedImage`): as images, 250 labels of
  100 × 150 mm would take over 2 GB (four bytes a dot). A job takes fewer
  labels than "Labels per job" once their PNGs reach 16 MB
  (`serial.BATCH_BYTES`), which only labels with large dithered images
  do. With the fake printer, 250 labels of 100 × 150 mm in one job: 2.3 GB
  of memory before, 0.26 GB packed. [measured]
- Whether `ESC s`'s labels to print are those of the whole job (all its
  `A`) or of the `A` being printed is not known. [open: the printout of 5
  labels was reported as 5 of 5; a count while a longer job runs and the
  count at a problem in the middle of a job are not confirmed yet] What a
  series' job printed does not depend on it (see "What a job printed"):
  a series stops at the first row not printed for certain and says so,
  as label by label, and "Continue from" goes on from that row.
- A series stops (the stop button, a cancelled job of the server)
  between two jobs: a job that has started is printed in full, up to
  "Labels per job" labels after the request. A problem cancels the job
  (`ESC c`) at once.
- With a fake printer that reads an image in 0.2 s and prints a 20 mm
  label in 0.4 s: 0.43 s a label instead of 1.0 s label by label.
  [measured with the fake printer]
- Labels whose variables change from copy to copy (`{=copy}`) go label
  by label, one job each.

## What a job printed

A job of one label and its copies (one `A`) counts its labels down in
`ESC s` [manual]; a series' job has an `A` for each label, and whether
`ESC s` counts all of them is not confirmed (above). The labels a job
printed are known this way:

- With the web interface's label counter (`/cgi-bin/get?cmd=3`, network
  only, read in 2 s or not at all): read just before the job is sent and
  while it prints, the difference is what it printed, exactly: also at
  a problem in the middle of a job (read after `ESC c`) and after a lost
  connection, when it is watched until it reached the job's labels or
  stayed the same for 10 s (`SETTLE_SECONDS`; the printer may print on
  a job whose `ESC c` did not reach it). A series' job is over once the
  counter counted every label, also with the interpreter still active
  (the next job need not wait for its second); a printer idle 10 s with
  fewer counted ended the job early, which is reported as a problem with
  the labels counted. A printer whose web interface does not answer is not asked
  again for 30 s. Another program printing at the same moment would
  count as ours (at most the job's labels). [decided] [open: whether the
  counter counts a label as it is printed or later; not watched during a
  print yet]
- Without it (USB, the web interface off or behind a password), from
  `ESC s`: a job of one label is over when the printer counted its
  labels down, as the manual has it; a series' job only once the printer
  is idle at two polls in a row, not when the labels to print reached 0
  with the interpreter still active (that may be one `A` of many), nor
  at a moment idle between two `A`. At a problem the labels
  printed for certain are those it was seen to count down (both readings
  agree on them: fewer than printed if it counts each `A` on its own), at
  most the job's labels minus those still to print in the answer that
  reports it; a job of one label counts exactly as the manual has it. A
  series' job counts as stuck only after 180 s and 10 s for each label
  still to print (`LABEL_SECONDS`), as a label printed may not show.
  Waiting for idle costs a series' job the interpreter's second after it
  and a poll: with a timed fake printer (0.2 s to read an image, 0.4 s a
  label, the interpreter active 1 s after a job) 45 labels in jobs of 20
  took 32.5 s instead of 28 s, in one job 29.5 s; with the label counter
  28 s as before, label by label 80 s either way. [decided] [measured with the fake
  printer]
- A lost connection (or a job that never ended): up to every label of
  the job may have printed, the ones counted before for certain. Cut off
  while the images were sent (before `m m`, which Tavletta sends on its
  own): nothing printed. [decided]
- When more may have printed than for certain, the message says so ("The
  job was sent, but its outcome is unknown: … up to N may have"), the
  counter goes on after every label that may have printed (a number
  skipped rather than one printed twice), and "Continue from" starts
  after the last label printed for certain: rows may print twice, none is
  left out, and the message says that too. The server's job tells
  `printed` (for certain) and `printed_max`.
- The model and resolution `qv` told are kept for 30 s, a job's last
  label for 2 s (the next label of a series does not wait for the
  interpreter's second after it). The web interface's serial number tells
  another printer at the address: both are dropped then. Without the web
  interface a printer swapped at the same network address within those
  30 s would be taken for the one before (on USB its address names its
  serial number, so it is another printer). [decided]

## Where the print lands

The test printer (EOS2/300, gap sensor, tear-off mode on, setup "Print
position Y" 0 mm) prints the image about 1.25 mm higher on the label (with
`O R`) than its place: the job places it at 0,0 on a label of the label's
size (`S l1;0,0,18.6,20,40` / `I:Label;0,0,0;…`), and the preview is that
image. A label for 40 × 18.6 mm laid out in cablabel S3 had its content
low in cablabel's own preview too (ink 3.4 mm from the top, 0.8 mm from
the bottom; Tavletta's render of it agrees within 0.07 mm) and comes out
centred from Tavletta [confirmed], as from cablabel for its users
[assumed]: the layout made up for the printer.
In Tavletta the label is laid out as it should come out (the preview
shows the content centred) and "Print offset" 1.25 makes up for the
printer; the job is then the one the old layout sent, dot for dot.
[confirmed by comparing the images]
Where the shift comes from (the gap sensor's distance to the head, the
tear-off feed) is not known; the setup's "Print position Y" would move
cablabel's prints too. [open]

## Label start, tear-off and backfeed

A print on the EOS2/300 that started in the gap or on the previous label
and used up to three labels for two one-label jobs came from a
hand-written test file, not from Tavletta or the printer's setup: with
the orientation "across", `length_mm` is the width across the head and
`tape_mm` the height along the feed (`Label.media_size`). The file had
`tape_mm` 40 and `length_mm` 17, so the job said `S l1;0,0,40,43,17` (40 mm
high, a label every 43 mm) on a roll of 50 × 20 mm labels; the corrected
file sends `S l1;0,0,18,20,48`. [confirmed] The window warns before
printing when the distance Tavletta sends (height + gap) differs from the
one `ESC l` reports ("Different labels loaded"); `tavletta print` says
the same on standard error and prints anyway.

What the manual says about the settings around the start of a label, for
later:

- Tear-off mode (`O T[x]` per job, setup "Tear-off mode"): after the job
  the last label is fed forward to the tear-off edge; the next job pulls
  it back first. [manual]
- Backfeed (setup "Backfeed": "optimized" or "always"): `O D` forces
  "always" for a job, which "feeds the label back and starts printing at
  the label margin"; `O P` is the "smart" mode that suppresses the backfeed
  ("a small horizontal white line" may appear where the label stopped
  under the head). [manual] Tavletta sends neither: the setup decides.
- `O` must directly follow `S`. [manual]
- `H` sets speed, heat, method, ribbon saver and backfeed speed; it does
  not change where a label starts. [manual]
- A printer that is not synchronised (`ESC l` → `N…`) feeds labels until
  it finds a gap before printing. [manual]

## Not used yet

- Continuous media (`S e`), cutter (`C` command, `qp` → `CUTTER`),
  peel-off and applicators.
- Printers with a wider head (SQUIX 6, MACH 6?) or two heads (XC, XD).
- Printer-side fonts, barcodes and text (`T`, `B`): Tavletta sends the
  rendered label as one image, so the preview stays exact.
