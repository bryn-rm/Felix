# Homepage fonts

These vendored WOFF2 files are the public homepage's font assets. Builds and
runtime use only the files committed here. No design export or external font
service is needed. The existing binaries are preserved unchanged.

## Attribution and licenses

All four families are distributed under the SIL Open Font License 1.1. The full
upstream notices and license terms are included beside the fonts:

| Files | Embedded version | Copyright / upstream project | Local license |
| --- | --- | --- | --- |
| `newsreader.woff2`, `newsreader-italic.woff2` | 1.003 | Copyright 2020 The [Newsreader Project Authors](https://github.com/productiontype/Newsreader) | [newsreader-OFL.txt](newsreader-OFL.txt) |
| `archivo.woff2` | 2.001 | Copyright 2020 The [Archivo Project Authors](https://github.com/Omnibus-Type/Archivo) | [archivo-OFL.txt](archivo-OFL.txt) |
| `ibm-plex-mono.woff2` | 2.3 | Copyright 2017 IBM Corp. All rights reserved. [IBM Plex](https://github.com/IBM/plex); reserved font name “Plex” | [ibmplexmono-OFL.txt](ibmplexmono-OFL.txt) |
| `caveat.woff2` | 2.000 | Copyright 2014 The [Caveat Project Authors](https://github.com/googlefonts/caveat) | [caveat-OFL.txt](caveat-OFL.txt) |

License files were retrieved on 2026-09-11 from the Google Fonts distribution:
[Newsreader](https://github.com/google/fonts/blob/main/ofl/newsreader/OFL.txt),
[Archivo](https://github.com/google/fonts/blob/main/ofl/archivo/OFL.txt),
[IBM Plex Mono](https://github.com/google/fonts/blob/main/ofl/ibmplexmono/OFL.txt),
and [Caveat](https://github.com/google/fonts/blob/main/ofl/caveat/OFL.txt).
Their copyright holders match the notices embedded in these binaries. Retain
these notices and license files when redistributing the fonts; consult the
included terms before modifying fonts with reserved names.

## Loading

[`../fonts.ts`](../fonts.ts) defines the families once with
[`next/font/local`](https://nextjs.org/docs/14/app/building-your-application/optimizing/fonts#local-fonts).
The homepage applies their generated CSS variables. Both editorial serif faces
and the other above-the-fold fonts are preloaded on this route. Newsreader uses
an adjusted Times New Roman fallback and Archivo an adjusted Arial fallback to
reduce font-swap layout shift. Mono and Caveat retain monospace/cursive fallbacks
instead of substituting a proportional sans serif.

Newsreader is variable in weight (200–800) and optical size (6–72); Archivo is
variable in weight (100–900). The loader retains the homepage's existing weight
ranges (300–500 and 400–600). IBM Plex Mono is regular 400; Caveat is medium 500.

## Binary identity (SHA-256)

These checksums identify the exact vendored builds independently of upstream
changes. When replacing a file, recheck its metadata, license, weights, and
rendering, and update its checksum.

- `archivo.woff2`: `7150c0ec5ad356453013d11affec1fbab95de0dd2dcecb043b4f1cb7f87c4ba4`
- `caveat.woff2`: `4588c4b2b9f7e1ca093720d17c1d89daf47a57f53a17e22a53eca2e7e8f7f156`
- `ibm-plex-mono.woff2`: `c36f509c0a8f9f85f29cb44bc8701d8a9e0b14c499e77a884f789ead7093a7ac`
- `newsreader-italic.woff2`: `a99fb127682b9af538780d21420037452197b0d37dfd273eb108f8a3665d5501`
- `newsreader.woff2`: `01817351be3edfc1714fe6d60ddea6a22a169a5ebd033b50c7f9495e5d9c386a`
