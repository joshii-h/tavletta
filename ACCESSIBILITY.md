# Accessibility

Tavletta should be usable by everyone who needs to print labels, including
people who use a screen reader, the keyboard only, high contrast or large
text. This page says what works today, what does not yet, and how to tell
us about a barrier.

## Commitment

- Accessibility problems are treated as bugs, not as wishes.
- New features are built with the accessibility support of GTK 4 and
  libadwaita, and follow the
  [GNOME accessibility guidelines](https://developer.gnome.org/hig/guidelines/accessibility.html).
- Barriers that keep someone from designing or printing a label come
  first.

## Supported environments

- **Desktop app**: Linux with GTK 4 and libadwaita (Flatpak or native),
  tested on GNOME and KDE Plasma.
- **Screen readers**: Orca through the GTK 4 accessibility interface
  (AT-SPI). Controls have names and descriptions; option pages, dialogs and
  toasts are announced.
- **Keyboard**: every function is reachable with the keyboard; the print
  dialog, the element list and the option pages follow the usual GNOME
  keys (Tab, arrow keys, Enter, Escape).
- **Arranging in the preview** (free layout): everything the mouse does
  has a keyboard way. With the preview focused, the arrow keys move the
  selected elements (Shift: in larger steps), Ctrl+A and Shift+Ctrl+A
  select all or none, Delete removes; opening an element's row in the
  element list selects it, and its **Position and size** page sets exact
  numbers; **Arrange** in the header bar aligns and distributes. The
  preview tells screen readers which element is selected and where it
  is. Handles and touch targets are at least 24 pixels (44 for a finger
  or pen); touch and pen drag like the mouse. Outlines are not told apart
  by colour alone (hover dashed, selection solid), and the overlay has no
  animation.
- **Display**: follows the system settings for dark style, high contrast
  and large text.
- **Languages**: the app is available in 27 languages.
- **Print server**: the REST API and MCP interface need no visual
  interface at all, so labels can also be printed from assistive tools or
  scripts; the API reference is plain HTML without scripts.

## Known limitations

- The label preview is an image. Its content is described in the element
  list beside it, but a screen reader cannot read the drawn label itself.
- Resizing has no keys of its own in the preview: sizes are set with the
  number fields of the element's row and its **Position and size** page.
- Colour choices on labels are checked for contrast on black-and-white
  printers, but there is no colour-blindness simulation of the preview yet.
- Printer status comes from the printers themselves; some models report
  only coarse states.

## Reporting a barrier

Please open an issue at
<https://git.joshuahirsig.xyz/joshii/tavletta/issues> (or on the GitHub
mirror) and describe what you tried, what you use (screen reader,
keyboard, magnifier, system settings) and what happened. If you prefer not
to report in public, write to conduct@joshuahirsig.xyz.

We answer every report and keep it open until the barrier is removed or a
workaround is documented.
