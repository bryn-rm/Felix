# Felix colour palette

Felix uses midnight navy, indigo, and warm ivory, with periwinkle for text on dark surfaces and small amber accents. This brings the public homepage and login into the same colour family as the working app. It retains the existing editorial typography.

| Role | Colour | Use |
| --- | --- | --- |
| Dark canvas | `#080f1e` | App canvas, homepage hero, login story |
| Dark panel | `#0d1526` | App navigation and panels |
| Primary | `#4f46e5` | Actions, feature labels on ivory, navigation selection |
| Primary hover | `#4338ca` | Primary action hover |
| On-dark accent | `#a5b4fc` | Hero emphasis, dark-panel labels and active navigation |
| Warm accent | `#fbbf24` | Public beta label and login privacy stamp |
| Light canvas | `#faf8f3` | Homepage body and sign-in panel |
| Light panel | `#eef2ff` | Approval explanation and sign-in hover |
| Light text | `#17213b` / `#526078` | Primary / secondary text on light surfaces |
| Dark text | `#f0f4ff` / `#a3b1cc` | Primary / secondary text on dark surfaces |

`frontend/src/app/globals.css` owns the shared colour tokens. Tailwind aliases reference these tokens; scoped public/login styles use them directly. Next viewport metadata repeats the navy hex because browser metadata cannot resolve CSS variables. Red, green, and amber status treatments in the app retain their existing meanings. Do not use the light periwinkle text colour on ivory or the dark indigo text colour on navy.

The choice is a design judgement, informed by:

- [Adobe’s complementary colour guidance](https://www.adobe.com/uk/creativecloud/design/discover/complementary-colors.html): cool blue/violet and warm yellow/orange families give the palette contrast; the warm accent is deliberately sparse.
- [Material colour roles](https://m3.material.io/styles/color/roles): reuse named surface, primary, and on-surface roles instead of selecting unrelated colours per page.
- [WCAG text contrast](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html): target at least 4.5:1 for normal text and 3:1 for large text. Check actual foreground/background pairs, including hover states.
