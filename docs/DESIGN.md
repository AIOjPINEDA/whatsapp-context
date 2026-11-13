# Visual identity

The homepage is designed like the operating manual for a small communications terminal. The dark cover carries a single illustration. The paper section explains the data flow. The dark installation section puts the actual commands in a terminal frame. For another project, keep the three roles and illustrate a device specific to that product.

| Role | Choice |
| --- | --- |
| Night and terminal surfaces | `#101a19`, `#14211f`, `#0a1211` |
| Printed section | `#e8ddc2` paper with `#18251f` ink |
| Text over dark surfaces | `#efe5cc` with `#b6c2ae` for secondary copy |
| Accents | `#bafa82` for actions, focus and the CRT signal. `#efb96b` for small markers. |
| Type | Barlow Condensed Bold for headings, IBM Plex Mono for controls and commands, system sans-serif for body copy |
| Shapes | Square corners, single-pixel rules and two corner marks on the cover |

The [page](index.html) holds the CSS variables, type and layout in one file. Both [typefaces](assets/fonts/) are served locally and include their SIL Open Font License notices. The illustration shifts below the title on narrow screens, where the page loads a smaller WebP. Keep the installation commands and translated copy as selectable text.

The [illustration](assets/rooftop-relay.webp) was generated with ImageGen. The prompt called for a 1990s hand-painted science-fiction anime still of a rooftop communications terminal above a city at night, with an ivory housing, green CRT waveform, amber indicator and dark petroleum sky. The [editable banner](assets/banner.svg) uses the same illustration and wordmark. Open it directly from the same static server as the page to load its relative assets. Export it at 1280 × 640 to [banner.png](assets/banner.png), which supplies the README and social card. The [logo](assets/logo.svg) and [favicon](assets/favicon.svg) use the waveform as a small-scale mark.
