"""Copy inheritable CSL name options onto each <name> element.

CSL lets a style set name options (name-as-sort-order, initialize-with, ...)
once on <style>, <citation> or <bibliography>. Pandoc's citeproc ignores the
style-level ones, so "Example A" comes out as "A Example". Running this over a
style writes the inherited values onto every <name> and <names> element, which
every CSL processor reads identically. Run it on any style added to this folder:

    python studio/csl/inline_name_options.py studio/csl/new-style.csl
"""
import sys
from lxml import etree

NS = "{http://purl.org/net/xbiblio/csl}"
NAME_ATTRS = ["and", "delimiter-precedes-et-al", "delimiter-precedes-last", "et-al-min",
              "et-al-use-first", "et-al-subsequent-min", "et-al-subsequent-use-first",
              "et-al-use-last", "initialize", "initialize-with", "name-as-sort-order",
              "sort-separator", "name-form"]


def inherited(el, attr):
    node = el.getparent()
    while node is not None:
        tag = etree.QName(node).localname
        if tag in ("citation", "bibliography", "style") and node.get(attr) is not None:
            return node.get(attr)
        node = node.getparent()
    return None


def context_root(el):
    node = el
    while node is not None:
        if etree.QName(node).localname in ("citation", "bibliography"):
            return node
        node = node.getparent()
    return None


def _options(root):
    bib = root.find(NS + "bibliography")
    opts = {}
    for attr in NAME_ATTRS:
        val = bib.get(attr) if bib is not None and bib.get(attr) is not None else root.get(attr)
        if val is not None:
            opts["form" if attr == "name-form" else attr] = val
    if root.get("name-delimiter"):
        opts["delimiter"] = root.get("name-delimiter")
    return opts


def patch(path):
    """Macros are shared between citation and bibliography, so they take the
    bibliography's values (the list is what an editor proofs), then the style's."""
    tree = etree.parse(path)
    root = tree.getroot()
    opts = _options(root)
    info = root.find(NS + "info")
    for names in root.iter(NS + "names"):
        if info is not None and info in names.iterancestors():
            continue
        if any(a is not None and etree.QName(a).localname == "substitute" for a in names.iterancestors()):
            continue  # a substituted <names> reuses the outer <name>
        if names.get("delimiter") is None and root.get("names-delimiter"):
            names.set("delimiter", root.get("names-delimiter"))
        name = names.find(NS + "name")
        if name is None:
            name = etree.Element(NS + "name")
            names.insert(0, name)
        for k, v in opts.items():
            if name.get(k) is None:
                name.set(k, v)
    tree.write(path, xml_declaration=True, encoding="utf-8")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        patch(p)
        print("patched", p)
