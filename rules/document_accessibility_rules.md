# Accessibility rules for document builders

These are the instructions an AI document builder follows when it makes a Word file, a
slide deck, a spreadsheet or a web page for Cedar Ridge College. They sit inside the builder's
own instructions, so accessibility is part of how the file is made rather than a review
afterwards. The checks in `checks/` then verify the finished file.

Target: WCAG 2.1 AA as the floor, WCAG 2.2 AA for new work. These rules encode accessibility
in the file's structure. Visual styling alone never carries it.

## Structure

- Use the real styles for structure: Title, Heading 1, Heading 2, Heading 3 and Normal in
  Word; h1 to h6 in a web page. Formatting can be layered on top of those styles. It never
  replaces them.
- Never make a heading out of bold or large body text. A screen reader reads it as one more
  sentence and the document outline skips it.
- Headings go down one level at a time. Heading 1 is followed by Heading 2, never Heading 3.
  To make a heading smaller, style a lower level; do not skip one.
- Mark the first row of every table as a header row (in Word, "Repeat as header row"; on a
  web page, `<th scope="col">`). Give a table that needs explaining a short caption or a
  lead-in sentence.
- A web page with more than one section wraps its content in `<main>` or starts with a skip
  link.

## Title and language

- Set the document title when the file is created: the Title property in Word, `<title>` on a
  web page. Use the document's real name, never a generic word like "Memo". It is the first
  thing a screen reader announces and the title bar of any PDF exported from the file.
- Declare the document language (`<html lang="en">`; the default language in Word). A passage
  in a second language carries its own language mark.

## Images

- Every image, chart, infographic, or logo used as content gets alt text that says what the
  image tells the reader. For a chart, that means the numbers: "Bar chart of RSVPs by week:
  40, 65, 90 and 120."
- Never leave the file name or a word a tool wrote ("Picture 3", "image.png") as alt text.
  Those count as missing.
- A purely decorative image is marked decorative (`alt=""` on the web; "Mark as decorative" in
  Word). It is never left ambiguous.
- Pair a chart with a visible caption or nearby text that carries the same point.

## Color and contrast

- Declare every text color pairing as data before building, in the file's color manifest:
  a label, the text color and the background color for each. Web pages embed it in a
  `<script type="application/json" id="wcag-contrast-manifest">` block; Word files keep it in
  a JSON file beside the build.
- Every text pairing clears 4.5:1, including labels, captions, metadata and table headers.
  The large-text exception (3:1) is not used.
- Use only colors from the declared manifest. Never type a color into the document anywhere
  else. This is what lets the contrast check, which reads the manifest, speak for the
  finished file.
- Icons, rules, chart axes, control borders and focus rings clear 3:1 against what they sit on.
  Declare them as `ui_pairings` on a web page.
- No gradients behind text and no text over photos without a solid panel. Contrast over a
  gradient has no single value, so it cannot be checked.
- Color is never the only carrier of meaning. A colored callout also says what it is in
  words ("Deadline:", "Action needed:").
- Gold (#B7791F) is an accent for rules and borders only. On white it measures 3.64:1, below
  the floor for text.

## Links and controls

- Link text says where the link goes. Never a raw web address and never "click here."
- Every button and link has a name a screen reader can announce.
- Never remove the keyboard focus outline without putting a visible replacement in its place.

## Finishing a file

- Save through the one finishing step that runs the checks: `python3 -m checks docx` for a
  Word file and `python3 -m checks web` for a page. A file that fails is not saved as done.
  Never save around the checks.
- When a check fails, fix the cause in the build and run it again. Do not edit the check, and
  do not hand over a file with a failure explained away.
- For a page someone has to sign off, run `python3 -m checks report` and hand over the report
  with the page.

## What still needs a person

The checks prove what a script can prove: that alt text exists, that headings are in order,
that declared colors clear the floor. A person still decides:

- whether alt text describes the image accurately;
- whether captions are complete;
- whether the reading order makes sense for the content;
- whether an image marked decorative really carries no information.

Tell the person receiving a Word file to open it and run Review > Check Accessibility before
it is posted or sent outside the office.
