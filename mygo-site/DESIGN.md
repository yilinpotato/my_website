# Design

## Direction

A bright band feature site built around MyGO!!!!!'s blue-and-white stage uniforms. White space and mist blue keep the page open; deep uniform blue grounds media sections, while member colors appear as small accents.

## Design Dials

- Design variance: 8
- Motion intensity: 6
- Visual density: 4

## Color Strategy

Full blue-and-white palette drawn from the band's stage costumes, with member-specific colors used only inside the interactive member area.

```css
--bg: oklch(0.975 0.012 235);
--surface: oklch(1 0 0);
--ink: oklch(0.25 0.055 242);
--muted: oklch(0.49 0.045 242);
--primary: oklch(0.48 0.12 240);
--deep-blue: oklch(0.22 0.075 246);
--line: oklch(0.84 0.025 240);
```

## Typography

Use a local-first Japanese and Chinese sans-serif stack. Large headlines use condensed spacing and strong weight. Body copy remains generous and highly readable.

## Shape

Cards and media frames use a consistent 14px radius. Buttons and compact controls may use full-pill shapes.

## Layout

- Full-bleed image-led hero
- Asymmetric band introduction
- Official music player and selectable official video player
- Official discography grid and reflective lyric interpretation section
- Interactive member stage rather than five repeated cards
- Film-strip anime still gallery
- Editorial group-moment gallery with lightbox viewing
- Paired character and performer rows for cast

## Motion

- One orchestrated hero entrance
- Member portrait crossfades and information changes
- Gentle reveal transitions triggered by IntersectionObserver
- No content is hidden by default
- All motion disabled or shortened with `prefers-reduced-motion`
