// Journal Typesetting Studio: article layout.
//
// Everything this file prints comes from data.json, which Studio writes from
// the approved article record and the journal profile. Text is never typed
// into Typst markup, so nothing has to be escaped and nothing can be
// reinterpreted as markup: an asterisk in a title stays an asterisk.
//
// One library serves every journal and article type. A journal changes the
// look through its profile (fonts, colours, margins, columns); an article type
// changes the first-page treatment through `variant`.

#let D = json("data.json")
#let S = D.style
#let C(key) = rgb(S.colors.at(key))
#let H = S.house

// ------------------------------------------------------------------ inlines

#let footnotes = D.footnotes

#let cite-parts(parts) = {
  for p in parts {
    if p.at(1) == none { p.at(0) } else { link(label(p.at(1)), p.at(0)) }
  }
}

#let inl(nodes) = {
  for n in nodes {
    let t = n.t
    if t == "text" { n.v }
    else if t == "emph" { emph(inl(n.c)) }
    else if t == "strong" { strong(inl(n.c)) }
    else if t == "sup" { super(inl(n.c)) }
    else if t == "sub" { sub(inl(n.c)) }
    else if t == "smallcaps" { smallcaps(inl(n.c)) }
    else if t == "link" { link(n.url, inl(n.c)) }
    else if t == "br" { linebreak() }
    else if t == "cite" {
      if D.cite_form == "superscript" { super(typographic: false, cite-parts(n.parts)) }
      else { [\[] + cite-parts(n.parts) + [\]] }
    }
    else if t == "xref" {
      if n.at("ok", default: false) { link(label(n.target), n.v) } else { n.v }
    }
    else if t == "math" {
      if n.at("typst", default: none) != none { math.equation(block: false, eval(n.typst, mode: "math")) }
      else { raw(n.tex) }
    }
    else if t == "fnref" { footnote(inl(footnotes.at(n.id))) }
  }
}

// ------------------------------------------------------------------ objects

#let fig-caption(obj) = [#text(weight: "bold", fill: C("primary"))[#obj.label.] #inl(obj.caption)]

#let render-figure(obj) = {
  let body = if obj.src == none {
    block(width: 100%, height: 3cm, stroke: 0.6pt + red, inset: 6pt)[Missing image: #obj.label]
  } else {
    // A full-width figure is capped in height so a chart does not take a page.
    if obj.wide { image(obj.src, width: 100%, height: 7.5cm, fit: "contain", alt: obj.alt) }
    else { image(obj.src, width: 100%, alt: obj.alt) }
  }
  let f = figure(body, caption: fig-caption(obj), numbering: none, kind: image, supplement: none,
                 placement: if obj.wide { top } else { none },
                 scope: if obj.wide { "parent" } else { "column" })
  [#f#label(obj.id)]
}

#let render-table(obj) = {
  let ncols = obj.cols
  let size = S.table_size * 1pt
  let header = obj.head.map(row => row.map(c => text(weight: "bold", inl(c))))
  let tbl = table(
    columns: if obj.wide { (auto,) * ncols } else { ncols },
    align: (x, y) => if x == 0 { left + horizon } else { center + horizon },
    stroke: (x, y) => (
      top: if y == 0 { 0.8pt + C("primary") } else if y == obj.head.len() { 0.5pt + C("primary") } else { 0pt },
      bottom: 0.3pt + C("rule"),
    ),
    fill: (x, y) => if y < obj.head.len() { C("primary").lighten(88%) } else if calc.odd(y) { luma(248) } else { none },
    inset: (x: 3.5pt, y: 3pt),
    table.header(repeat: true, ..header.flatten()),
    ..obj.rows.map(r => r.map(c => inl(c))).flatten(),
  )
  let note = if obj.note.len() > 0 { block(above: 4pt, text(size: size - 0.5pt, inl(obj.note))) }
  let content = align(left, text(size: size, [#set par(justify: false, first-line-indent: 0pt); #tbl #note]))
  if obj.long {
    // A long table leaves the columns and may run over several pages,
    // repeating its header row on each.
    page(columns: 1)[
      #set block(breakable: true)
      #show figure: set block(breakable: true)
      #[#figure(content, caption: fig-caption(obj), numbering: none, kind: table, supplement: none)#label(obj.id)]
    ]
  } else {
    let f = figure(content, caption: fig-caption(obj), numbering: none, kind: table, supplement: none,
                   placement: if obj.wide { top } else { none },
                   scope: if obj.wide { "parent" } else { "column" })
    [#f#label(obj.id)]
  }
}

#let render-equation(obj) = {
  let eq = if obj.typst != none {
    math.equation(block: true, numbering: none, eval(obj.typst, mode: "math"), alt: obj.alt)
  } else { raw(obj.tex, block: true) }
  [#block(width: 100%, breakable: false, grid(columns: (1fr, auto), align: (center + horizon, right + horizon), eq, obj.label))#label(obj.id)]
}

// ------------------------------------------------------------------ blocks

#let blocks(bs) = {
  for b in bs {
    let t = b.t
    if t == "heading" { heading(level: b.level, inl(b.c)) }
    else if t == "para" { par(inl(b.c)) }
    else if t == "list" {
      let items = b.items.map(it => blocks(it))
      if b.ordered { enum(..items) } else { list(..items) }
    }
    else if t == "quote" { quote(block: true, blocks(b.blocks)) }
    else if t == "figure" { render-figure(D.figures.at(b.id)) }
    else if t == "table" { render-table(D.tables.at(b.id)) }
    else if t == "equation" { render-equation(D.equations.at(b.id)) }
  }
}

// ------------------------------------------------------------------ front matter

#let orcid-mark(id) = link("https://orcid.org/" + id, box(baseline: 1pt,
  circle(radius: 3.6pt, fill: rgb("#a6ce39"), stroke: none,
    align(center + horizon, text(size: 4.2pt, fill: white, weight: "bold", font: S.fonts.sans)[iD]))))

#let author-line = {
  let parts = D.authors.map(a => {
    let marks = a.sup + if a.corresponding { "*" } else { "" }
    [#a.name#super(marks)#if a.orcid != none [ #orcid-mark(a.orcid)]]
  })
  parts.join([, ])
}

#let masthead = {
  let m = D.masthead
  let band = block(width: 100%, fill: C("primary"), inset: (x: 8pt, y: 7pt), radius: 2pt, {
    set text(fill: white, font: S.fonts.sans)
    grid(columns: if m.logo != none { (26pt, 1fr, auto) } else { (1fr, auto) }, column-gutter: 8pt, align: horizon,
      ..if m.logo != none { (image(m.logo, height: 26pt, alt: m.journal_title + " logo"),) } else { () },
      [#text(size: 11.5pt, weight: "bold", m.journal_title) \ #text(size: 7pt, m.issn_line + if m.issn_line != "" and m.url != "" [ · ] + link(m.url, m.url))],
      align(right, text(size: 7pt, m.publisher)))
  })
  if H.masthead == "boxed" {
    // Logo at the left, journal name large, ISSNs at the right, all inside one ruled box.
    block(width: 100%, stroke: 0.8pt + C("ink"), inset: 0pt, grid(
      columns: if m.logo != none { (62pt, 1fr) } else { (1fr,) },
      ..if m.logo != none { (block(inset: 6pt, image(m.logo, width: 50pt, alt: m.journal_title + " logo")),) } else { () },
      block(width: 100%, stroke: (left: 0.8pt + C("ink")), inset: (x: 6pt, y: 4pt), {
        set text(font: S.fonts.sans, fill: C("ink"))
        let (pre, main) = if m.journal_title.starts-with("International Journal of ") {
          ("International Journal of", m.journal_title.slice(25))
        } else { (none, m.journal_title) }
        if pre != none { text(size: 8pt, pre); linebreak() }
        text(size: 20pt, main)
        grid(columns: (1fr, auto), align: (left + bottom, right + bottom),
          text(size: 7.5pt, link(m.url, m.url.replace("https://", "").replace("http://", ""))),
          text(size: 7.5pt, m.issn_lines.join(linebreak())))
      })))
  } else { band }
  v(5pt)
  grid(columns: (1fr, auto), align: (left + horizon, right + horizon),
    text(font: S.fonts.sans, size: 8pt, weight: "bold", fill: C("accent"), upper(D.article_type_label)),
    text(font: S.fonts.sans, size: 7.5pt, fill: C("muted"), D.article_line))
  if D.doi_url != none {
    align(right, text(font: S.fonts.sans, size: 7.5pt, link(D.doi_url, D.doi_url)))
  } else if D.doi_placeholder != none {
    align(right, text(font: S.fonts.sans, size: 7.5pt, [DOI: #D.doi_placeholder]))
  }
}

#let abstract-block = {
  if D.abstract == none { return }
  let heading-text = text(font: S.fonts.heading, weight: "bold", size: 9pt, fill: C("primary"),
    if H.abstract_heading != none { H.abstract_heading } else { D.abstract.heading })
  let secs = for s in D.abstract.sections {
    par(justify: true)[#if s.label != none [#text(weight: "bold", fill: C("primary"))[#s.label:] ]#inl(s.c)]
  }
  let kw = if D.keywords != "" { par[#text(weight: "bold", fill: C("primary"))[#H.keywords_label:] #D.keywords] }
  let inner = [#heading-text #v(2pt) #set text(size: S.size * 0.92pt); #set par(first-line-indent: 0pt); #secs #kw]
  if H.abstract_box == "framed" {
    // Double rule frame in the accent colour.
    block(width: 100%, stroke: 2.2pt + C("accent"), inset: 2pt,
      block(width: 100%, stroke: 0.6pt + C("accent"), inset: 8pt, inner))
  } else if D.variant == "research" {
    block(width: 100%, fill: C("primary").lighten(92%), inset: 8pt, radius: 2pt, inner)
  } else if D.variant == "review" {
    block(width: 100%, inset: (y: 7pt), stroke: (top: 1pt + C("primary"), bottom: 1pt + C("primary")), inner)
  } else if D.variant == "case-report" {
    block(width: 100%, inset: (left: 9pt, y: 4pt), stroke: (left: 3pt + C("accent")), inner)
  } else { inner }
}

#let front = {
  masthead
  v(8pt)
  block(par(justify: H.title_align == "justify", first-line-indent: 0pt,
    text(font: S.fonts.heading, size: if D.variant == "editorial" { 20pt } else { H.title_size * 1pt }, weight: "bold",
       fill: C(H.heading_color), hyphenate: false, if H.title_case == "upper" { upper(inl(D.title)) } else { inl(D.title) })))
  if D.title_original != none {
    block(above: 4pt, text(size: 12pt, style: "italic", fill: C("muted"), lang: D.title_original_lang, inl(D.title_original)))
  }
  v(4pt)
  block(text(size: 10.5pt, author-line))
  block(above: 5pt, {
    set text(size: 7.5pt, fill: C("muted"))
    for a in D.affiliations [#super(a.label)#a.text \ ]
    if D.correspondence != none [#text(fill: black)[\*Correspondence:] #D.correspondence \ ]
    if D.dates.len() > 0 { D.dates.map(d => [#d.at(0): #d.at(1)]).join([ · ]) }
  })
  v(6pt)
  abstract-block
  v(6pt)
  line(length: 100%, stroke: 0.5pt + C("rule"))
  v(4pt)
}

// ------------------------------------------------------------------ document

#set document(title: D.document_title, author: D.meta_authors, keywords: D.keyword_list)
#set text(font: S.fonts.body, size: S.size * 1pt, lang: D.lang, hyphenate: true)
#set par(justify: true, leading: 0.55em, spacing: 0.75em, first-line-indent: (amount: H.first_line_indent * 1em, all: false))
#show heading: set text(font: S.fonts.heading, fill: C(H.heading_color))
#show heading.where(level: 1): it => block(above: 1.1em, below: 0.6em, sticky: true, text(size: S.size * 1.12pt, weight: "bold", it.body))
#show heading.where(level: 2): it => block(above: 0.9em, below: 0.5em, sticky: true, text(size: S.size * 1pt, weight: "bold", style: "italic", it.body))
#show heading.where(level: 3): it => block(above: 0.8em, below: 0.4em, sticky: true, text(size: S.size * 1pt, style: "italic", it.body))
#show figure.where(kind: table): set figure.caption(position: top)
#show figure.caption: set align(left)
#show figure.caption: set text(size: S.size * 0.85pt)
#show figure: set block(above: 10pt, below: 10pt)
#show link: it => if type(it.dest) == str { text(fill: C("primary"), it) } else { it }
#set footnote.entry(separator: line(length: 30%, stroke: 0.4pt))
#show footnote.entry: set text(size: 7.5pt)

#let first-footer = {
  set text(size: 6.5pt, fill: C("muted"), font: S.fonts.sans)
  line(length: 100%, stroke: 0.3pt + C("rule"))
  grid(columns: (1fr, auto), column-gutter: 10pt,
    if D.license != none [#D.license.statement #if D.license.url != none [#link(D.license.url, D.license.url)]] else [#text(fill: red)[No licence configured]],
    context counter(page).display())
}

#set page(
  paper: S.page,
  margin: (top: S.margins.top * 1pt, bottom: S.margins.bottom * 1pt, inside: S.margins.inside * 1pt, outside: S.margins.outside * 1pt),
  columns: S.columns,
  header: context {
    let p = counter(page).get().first()
    if H.header_style == "left-italic" {
      set text(size: 7.5pt, fill: C("muted"))
      grid(columns: (1fr, auto), align: (left + bottom, right + bottom),
        text(style: "italic", if calc.even(p) { D.header.even } else { D.header.odd }), text(size: 9pt, str(p)))
    } else if p > 1 {
      set text(size: 7pt, fill: C("muted"), font: S.fonts.sans)
      grid(columns: (1fr, 1fr), align: (left, right),
        if calc.even(p) [#p #h(6pt) #D.header.even] else [#D.header.odd],
        if calc.even(p) [#D.header.odd] else [#D.header.even #h(6pt) #p])
      v(-4pt)
      line(length: 100%, stroke: 0.3pt + C("rule"))
    }
  },
  footer: context {
    let p = counter(page).get().first()
    if p == 1 { first-footer } else {
      set text(size: 6.5pt, fill: C("muted"), font: S.fonts.sans)
      align(center, D.footer)
    }
  },
  background: if D.watermark != none {
    rotate(-40deg, text(size: 46pt, fill: rgb(200, 30, 30, 40), weight: "bold", font: S.fonts.sans, D.watermark))
  },
)
#set columns(gutter: S.column_gap * 1pt)

#place(top, float: true, scope: "parent", clearance: 10pt, front)

#blocks(D.body)

// ------------------------------------------------------------------ back matter

#if D.supplementary.len() > 0 {
  heading(level: 2)[Supplementary Material]
  set par(justify: false)
  for s in D.supplementary [#par[#s.label: #link(s.url, s.url)#if s.description != "" [ (#s.description)]]]
}

#for d in D.declarations {
  block(above: 0.9em, below: 0.3em, sticky: true, text(font: S.fonts.heading, size: S.size * 0.95pt, weight: "bold", fill: C("primary"), d.heading))
  set text(size: S.size * 0.92pt)
  blocks(d.blocks)
}

#if D.references.len() > 0 {
  heading(level: 1)[References]
  set text(size: S.size * 0.85pt)
  set par(first-line-indent: 0pt, justify: false)
  for r in D.references {
    [#block(above: 3pt, grid(columns: (1.8em, 1fr), [#r.label.], inl(r.c)))#label(r.id)]
  }
}
