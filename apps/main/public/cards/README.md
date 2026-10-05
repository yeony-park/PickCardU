# Card images

The product list is based on the source PDFs in `data/raw`. The original PDFs are unchanged. `apps/main/lib/card-catalog.json` records each product's source PDF, local image, original image URL, and source page.

Images were retrieved from public card product pages: CardGorilla, Toss Card Lounge, Banksalad, Hana Card, BC Card, and HKU's Green Card case study. Their ownership remains with the respective rights holders. Serving local copies avoids runtime dependencies on those sites.

Bundled products use a labelled representative design: KB 가온·누리, 하나 원더카드 2.0, and IBK BLISS.5. BC BIZ CORPORATE uses Hana's issuing-bank design with a visible label. Biz Air Money has no verified image and displays an explicit unavailable state; no unrelated card or fabricated plate is substituted.

Benefit rates, annual fees and spending conditions are read from the linked source PDF, rather than the old mock data or a potentially different current product version. The list includes historical products and is not an assertion of current issuance availability.
