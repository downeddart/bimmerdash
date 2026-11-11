---
name: bimmerdash
description: A dark, glanceable dashboard for one BMW, its fuel, its miles and its health.
colors:
  dash-blue: "#5aa2ff"
  dash-blue-bright: "#8cbeff"
  dash-blue-wash: "#1a2130"
  amber: "#f2b33d"
  amber-wash: "#2a2212"
  ok-green: "#3dd68c"
  ok-wash: "#10261b"
  alarm-red: "#ff6b6b"
  alarm-wash: "#2a1414"
  alarm-line: "#5c2626"
  alarm-text: "#ffb4b4"
  garage-floor: "#0b0d10"
  nav-shadow: "#0e1014"
  panel: "#13161b"
  panel-raised: "#171b22"
  panel-top: "#1a1f27"
  rule: "#1c2027"
  rule-strong: "#232a34"
  bar-track: "#2a3340"
  control-edge: "#68717f"
  idle-grey: "#68717f"
  headlight-white: "#e6e8eb"
  muted-steel: "#9ba3af"
typography:
  display:
    fontFamily: "Space Grotesk, Inter, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 600
    letterSpacing: "-0.02em"
  numeral-lg:
    fontFamily: "Space Grotesk, Inter, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 600
  numeral:
    fontFamily: "Space Grotesk, Inter, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 600
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    letterSpacing: "0.04em"
  caption:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
rounded:
  xs: "6px"
  sm: "10px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  hero: "24px"
  full: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
components:
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.xl}"
    padding: "24px"
  stat:
    backgroundColor: "{colors.panel-raised}"
    textColor: "{colors.headlight-white}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  button:
    backgroundColor: "{colors.panel-top}"
    textColor: "{colors.headlight-white}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
    height: "40px"
  button-ghost:
    textColor: "{colors.muted-steel}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
    height: "40px"
  button-primary:
    backgroundColor: "{colors.dash-blue}"
    textColor: "{colors.garage-floor}"
    rounded: "{rounded.sm}"
    padding: "12px"
    height: "46px"
  button-primary-hover:
    backgroundColor: "{colors.dash-blue-bright}"
  input:
    backgroundColor: "{colors.garage-floor}"
    textColor: "{colors.headlight-white}"
    rounded: "{rounded.sm}"
    padding: "9px 12px"
    height: "42px"
  segmented:
    backgroundColor: "{colors.garage-floor}"
    textColor: "{colors.muted-steel}"
    rounded: "{rounded.full}"
    padding: "3px"
  segmented-on:
    backgroundColor: "{colors.panel-top}"
    textColor: "{colors.headlight-white}"
    rounded: "{rounded.full}"
    padding: "8px 16px"
  pill:
    backgroundColor: "{colors.panel-top}"
    rounded: "{rounded.full}"
    padding: "6px 12px"
  pill-ok:
    backgroundColor: "{colors.ok-wash}"
    textColor: "{colors.ok-green}"
    rounded: "{rounded.full}"
    padding: "6px 12px"
  tag-estimate:
    backgroundColor: "{colors.amber-wash}"
    textColor: "{colors.amber}"
    rounded: "{rounded.xs}"
    padding: "1px 6px"
  nav-link-active:
    backgroundColor: "{colors.panel-raised}"
    textColor: "{colors.headlight-white}"
    rounded: "{rounded.sm}"
    padding: "11px 12px"
    height: "44px"
---

# Design System: bimmerdash

## Overview

**Creative North Star: "The Night Garage"**

A quiet workshop after dark. The car sits under one good light, the gauges glow, and
everything else falls back into near-black. bimmerdash is a set of dark panels stacked a
few shades apart, with the numbers doing the talking: large Space Grotesk figures,
small muted labels, and colour held back for the few things that need it: the one blue
that means "this is live or interactive", amber for fuel and anything estimated, green
for fine, red for wrong.

It is an Operate surface used two ways: a phone glance (fuel, cheapest station, locked,
anything wrong) and a desktop review (trips on maps, costs over months). Density stays
moderate: a glance has to land in a second, so each card leads with one figure and keeps
its explanation in a muted line beneath.

The incumbent look is soft: generous rounded cards and tonal layering. The chosen
direction for new work is **precise and technical**: tighter radii, hairline rules and
instrument-like detail, applied within the same palette. It is a direction, not yet a
rebuild; existing cards keep their shape until a deliberate pass changes them.

Explicitly not a Grafana-style panel grid (owner's requirement): no chart-per-tile walls,
no default chart palettes, no chrome around every panel.

**Key Characteristics:**
- Near-black tonal layers instead of shadows
- One blue accent; amber, green and red only as state
- Numbers in Space Grotesk, words in Inter
- Muted secondary lines carry the explanation; the figure carries the answer
- Estimates are always visibly marked in amber

## Colors

A near-monochrome blue-black garage, lit by one instrument blue and three state colours.

### Primary
- **Dash Blue** (`dash-blue`): links, the active nav brand mark, focus rings, primary
  buttons, map routes and default chart bars. The only colour that means "interactive"
  or "current".
- **Dash Blue Bright** (`dash-blue-bright`): hover state of Dash Blue links and buttons.
- **Dash Blue Wash** (`dash-blue-wash`): faint blue ground behind selected or highlighted
  items.

### Secondary
- **Fuel Amber** (`amber`, CSS `--fuel` and `--warn`): fuel level and fuel-related
  figures, "due soon" and "low" states, and every estimated value (the "~£" amounts and
  the "UK avg" tag).
- **Amber Wash** (`amber-wash`): background of amber tags and warning pills.

### Tertiary
- **OK Green** (`ok-green`) on **OK Wash** (`ok-wash`): healthy, locked, connected,
  within target.
- **Alarm Red** (`alarm-red`, CSS `--bad`): overdue, very low, failed. Text and dots
  only; never a large fill.
- **Alarm Wash, Alarm Line, Alarm Text** (`alarm-wash`, `alarm-line`, `alarm-text`, CSS
  `--bad-bg`, `--bad-line`, `--bad-text`): error boxes and inline error messages.

### Neutral
- **Garage Floor** (`garage-floor`): page background, input and segmented-control wells.
- **Nav Shadow** (`nav-shadow`): the side navigation, a half step above the floor.
- **Panel** (`panel`): cards.
- **Panel Raised** (`panel-raised`): stat blocks and list items inside cards, the active
  nav link.
- **Panel Top** (`panel-top`): buttons, pills, the selected segment.
- **Rule** (`rule`) and **Rule Strong** (`rule-strong`): card borders and dividers;
  input, button and hero borders.
- **Bar Track** (`bar-track`): unfilled part of progress bars.
- **Control Edge** (`control-edge`, CSS `--control-line`): the border of inputs and
  selects and the edge of a selected segment; at least 3:1 against every panel.
- **Idle Grey** (`idle-grey`, CSS `--idle`): dots and markers for cleared, unknown or
  inactive items; at least 3:1 against every panel.
- **Headlight White** (`headlight-white`): primary text.
- **Muted Steel** (`muted-steel`): labels, units, explanations, inactive nav links.

### Named Rules
**The One Light Rule.** Dash Blue is the only accent. A new feature does not get its own
hue; it gets a state colour or none.

**The Honest Amber Rule.** Anything not measured from this car (UK average prices,
assumed economy, projections) is shown in amber and labelled. A grey or white estimate
is a bug.

**The Layer, Don't Light Rule.** Depth comes from stepping up through Garage Floor →
Panel → Panel Raised → Panel Top, never from glows or shadows.

**The Edges Are Visible Rule.** Tonal steps are for surfaces. Anything a user operates
(fields, selected options) is outlined in Control Edge so it reads at 3:1.

## Typography

**Display Font:** Space Grotesk (with Inter, system-ui)
**Body Font:** Inter (with system-ui)

**Character:** Space Grotesk's squared, slightly mechanical figures read like an
instrument readout; Inter keeps every label and sentence quiet and legible. Both are
self-hosted via Fontsource (Inter 400/500/600, Space Grotesk 500/600).

### Hierarchy
- **Display** (600, 34px, -0.02em): page titles only, one per page.
- **Numeral Large** (600, 26px): the headline figure of a tile.
- **Numeral** (600, 18–24px): secondary figures in stat blocks and the hero bar.
- **Title** (600, 16px): card headings.
- **Body** (400, 14px): base text, list rows, explanations.
- **Label** (500, 13px, 0.04em, uppercase): eyebrows above titles and tiles; a plain,
  sentence-case variant is used for page subtitles.
- **Caption** (400, 12px): sources, timestamps, units of context, in Muted Steel.
- **Units** are set at 0.55em of their figure, weight 500, Muted Steel, never wrapped.

### Named Rules
**The Figures Are Grotesk Rule.** Every number a user reads as a measurement (miles,
mpg, £, psi, %) is set in Space Grotesk via the `num` class. Prose numbers stay in Inter.

## Layout

A side navigation (232px basis, sticky on wide screens) beside a fluid main column
padded `32px clamp(16px, 3vw, 40px) 48px`, with 24px between sections. At 820px and
below the nav becomes a top bar with a horizontally scrolling link row and a compact
stream-status strip.

Content flows in auto-fit grids (`minmax(min(100%, var(--min, 200px)), 1fr)`, 16px gap),
so cards reflow from four across to one without fixed breakpoints. Two-column sections
use a 380px minimum. Page-specific breakpoints exist at 480, 520, 600, 900 and 960px.
Spacing follows a 4px base with 8 / 12 / 16 / 24 / 32 as the common steps.

Interactive targets are at least 40px tall (44px for nav links and login inputs).

## Elevation & Depth

Flat. There are no shadows anywhere in the interface; depth is conveyed by tonal
layering of the neutral ramp plus 1px borders in Rule or Rule Strong. The only
"lighting" is the car render on the Overview hero, which sits on a slightly bluer ground
(`#1a2029`) to read as a lit bay.

### Named Rules
**The No-Glow Rule.** No box-shadows, no blurred glows, no gradients behind content. If
something needs to stand forward, it moves one step up the panel ramp.

## Shapes

Rounded rectangles throughout, with radius growing with the element: 6px for tags and
small chips, 10px for buttons, inputs and nav links, 12px for stat blocks and inner
panels, 16px for tiles, 20px for cards, 24px for the Overview hero, and full pills for
status pills and segmented controls. Dots are 8px circles. Chart bars have a 6px top and
3px bottom radius.

### Named Rules
**The Tighter-Going-Forward Rule.** New components favour the lower end of the scale
(6–12px) and 1px hairline rules over larger, softer shapes. Do not enlarge radii; when a
component is reworked, it moves toward precise, not softer.

## Components

### Buttons
Quiet, bordered, and small; the interface has almost no loud buttons.
- **Shape:** gently rounded (10px), at least 40px tall, 13px text.
- **Default:** Panel Top fill with a Rule Strong border; border turns Dash Blue on hover.
- **Ghost:** no fill, Muted Steel text, same border. For cancel, hide, forget.
- **Primary:** Dash Blue fill with Garage Floor text, weight 600, used only for sign-in
  and account creation. Hover moves to Dash Blue Bright; disabled drops to 60% opacity.

### Segmented control
The unit switcher (miles or kilometres, mpg or L/100 km), on Fuel and Settings.
- **Style:** a full-pill well in Garage Floor with a Rule Strong border and 3px inset.
- **States:** options are Muted Steel text; the selected option fills with Panel Top,
  gains a 1px Control Edge border and Headlight White text. `aria-pressed` reflects the
  state.

### Pills and tags
- **Pill:** full-pill status chip, 12px weight 500. Neutral on Panel Top; OK on OK Wash
  in OK Green; warning on Amber Wash in Amber.
- **Estimate tag:** a tiny 6px-radius tag on Amber Wash in Amber, 11px weight 600
  ("UK avg"). Always paired with the estimate it qualifies.

### Cards / Containers
- **Corner Style:** 20px.
- **Background:** Panel, with a 1px Rule border.
- **Shadow Strategy:** none (see Elevation).
- **Internal Padding:** 24px; tiles use 18–20px.
- **Inner blocks:** stat blocks on Panel Raised at 12px radius; lists separate rows with
  a 1px Rule top border and no outer frame.

### Inputs / Fields
- **Style:** Garage Floor well, 1px Control Edge border, 10px radius, 42–44px tall.
  Placeholders in Muted Steel. Shared styles live in `app.css`; pages set widths only.
- **Focus:** border turns Dash Blue; the global focus ring is a 2px Dash Blue outline at
  2px offset.
- **Settings rows:** label and explanation on the left, control on the right, rows
  divided by Rule; they wrap to stacked on narrow screens.

### Navigation
- **Style:** Nav Shadow column with a Rule border, 30px Dash Blue brand mark at 9px
  radius, Space Grotesk wordmark at 19px.
- **Links:** Muted Steel, weight 500, 44px tall with a 12px icon gap; hover to Headlight
  White; active link on Panel Raised.
- **Status box:** stream connection status pinned to the bottom of the nav.
- **Mobile:** collapses to a top bar with horizontally scrolling links.

### Car hero (signature)
The Overview hero: BMW's own render of the car centred in a 24px-radius bay on a bluer
ground, with a Panel-dark bar along the bottom carrying the name and three headline
figures in Space Grotesk.

### Dark map (signature)
Maps use standard OpenStreetMap tiles inverted and desaturated by CSS filter rather than
a third-party dark style, with Dash Blue routes and controls restyled onto Panel Raised.

### Status dot list (signature)
Health and alert lists lead each row with an 8px state dot (green, amber, red, or Idle
Grey for cleared), a weight-500 title, a Muted Steel caption, and the state word in its
colour at the right.

## Do's and Don'ts

### Do:
- **Do** lead every card with one figure in Space Grotesk and explain it in a Muted
  Steel caption below.
- **Do** mark every estimate in Amber with a label ("~£", "UK avg", "assumed").
- **Do** use the CSS variables in `app.css`; add a variable before reusing a new hex.
- **Do** give charts a text equivalent: each bar is a list item with its value in a
  visually hidden label.
- **Do** keep interactive targets at 40px or taller and keep the 2px Dash Blue focus
  ring.
- **Do** build new components toward the tighter end of the radius scale (6–12px).

### Don't:
- **Don't** build Grafana-style walls of chart tiles or use a charting library's default
  palette.
- **Don't** add shadows, glows or gradients to create depth.
- **Don't** introduce a second accent hue; use a state colour or neutral.
- **Don't** show an estimated number in white or grey.
- **Don't** fill large areas with Alarm Red; red is for text and dots.
- **Don't** mark state with a thick coloured side border; use the state dot.
- **Don't** wrap readable text in `role="img"`; it hides the values from screen
  readers.
