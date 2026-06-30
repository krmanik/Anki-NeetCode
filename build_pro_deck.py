#!/usr/bin/env python3
"""Build the Anki-NeetCode Pro deck (Anki-NeetCode-Pro.apkg).

This is the committed, runnable version of the build cells in main.ipynb.
It regenerates the per-card test cases from the authoritative LeetCodeDataset
`test` field and writes the apkg plus inspectable test files under data/test-code/.

Run:  python3 build_pro_deck.py
"""
import json, re, ast, gzip, zipfile, sqlite3, tempfile, os, shutil
import genanki
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DATASET = "data/LeetCodeDataset-v0.3.1-train.jsonl.gz"
NEETCODE_LIST = "neetcode-150-list.json"
APKG_OUT = "Anki-NeetCode-Pro.apkg"
TEST_CODE_DIR = "data/test-code"

# Design / class-based problems whose harness is operation-sequences, not
# check(callable) — the dataset has no entry for them, tests stay empty.
paidOnly = ['alien-dictionary', 'encode-and-decode-strings', 'graph-valid-tree',
            'meeting-rooms-ii', 'meeting-rooms',
            'number-of-connected-components-in-an-undirected-graph', 'walls-and-gates']


# --------------------------------------------------------------------------
# Test-code generation
# --------------------------------------------------------------------------
# We keep the dataset's `test` asserts verbatim (keyword args + tree_node /
# list_node helpers that ship in `prompt`) but wrap them so the card reports a
# per-case pass/fail summary instead of dying on the first failed assert.
#
# The generated TestCode is stored as plain, editable Python in the note. The card
# (front-pro.html) reads it from a hidden <script type="text/x-python"> element, so
# arbitrary characters (backslashes, <, >, &, quotes) survive verbatim and users
# can still read / edit / add test cases directly in the TestCode field.
#
# Some problems accept MULTIPLE valid answers (the dataset inputs don't satisfy the
# "unique solution" guarantee) or return collections whose order is irrelevant. For
# those, exact `==` against the single reference output wrongly fails a correct
# solution. We rewrite their asserts to use a tolerant comparison:
#   'outer'       - top-level list order irrelevant (multiset); inner order kept
#   'outer_inner' - top-level and each nested list order irrelevant (multiset)
#   'two_sum_0' / 'two_sum_1' - validate returned indices sum to target (0/1-indexed)
COMPARE_MODES = {
    'two-sum': 'two_sum_0',
    'two-sum-ii-input-array-is-sorted': 'two_sum_1',
    'subsets': 'outer_inner',
    'subsets-ii': 'outer_inner',
    'combination-sum': 'outer_inner',
    'combination-sum-ii': 'outer_inner',
    '3sum': 'outer_inner',
    'group-anagrams': 'outer_inner',
    'permutations': 'outer',
    'palindrome-partitioning': 'outer',
    'generate-parentheses': 'outer',
    'letter-combinations-of-a-phone-number': 'outer',
    'word-search-ii': 'outer',
    'n-queens': 'outer',
    'top-k-frequent-elements': 'outer',
    'pacific-atlantic-water-flow': 'outer',
}

COMPARE_HELPERS = (
    "def _sk(o):\n"
    "    return (type(o).__name__, repr(o))\n"
    "def _canon(x, deep):\n"
    "    if isinstance(x, (list, tuple)):\n"
    "        e = [_canon(i, deep) for i in x]\n"
    "        if deep:\n"
    "            e = sorted(e, key=_sk)\n"
    "        return tuple(e)\n"
    "    return x\n"
    "def _eq_outer(a, b):\n"
    "    if a is None or b is None:\n"
    "        return a == b\n"
    "    return sorted((_canon(x, False) for x in a), key=_sk) == sorted((_canon(x, False) for x in b), key=_sk)\n"
    "def _eq_outer_inner(a, b):\n"
    "    if a is None or b is None:\n"
    "        return a == b\n"
    "    return sorted((_canon(x, True) for x in a), key=_sk) == sorted((_canon(x, True) for x in b), key=_sk)\n"
    "def _valid_two_sum(res, arr, target, one_indexed, expected):\n"
    "    if expected is None:\n"
    "        return res is None or res == [] or res == ()\n"
    "    if not res or len(res) != 2:\n"
    "        return False\n"
    "    off = 1 if one_indexed else 0\n"
    "    i, j = res[0] - off, res[1] - off\n"
    "    n = len(arr)\n"
    "    if not (0 <= i < n and 0 <= j < n and i != j):\n"
    "        return False\n"
    "    return arr[i] + arr[j] == target\n\n"
)


import copy as _copy


def _to_positional(call):
    """candidate(a=x, b=y) -> candidate(x, y) so any param naming works."""
    if call.keywords:
        call.args = list(call.args) + [k.value for k in call.keywords]
        call.keywords = []


class _PosCall(ast.NodeTransformer):
    def visit_Call(self, n):
        self.generic_visit(n)
        if isinstance(n.func, ast.Name) and n.func.id == 'candidate':
            _to_positional(n)
        return n


def _rewrite_assert(test_src, node, mode):
    """Return tolerant, positional-call source for one assert statement.

    Always converts candidate(kw=...) calls to positional so solutions with
    different parameter names (NeetCode vs LeetCode) still run. For flagged
    problems also swaps exact `==` for an order-insensitive / semantic check.
    """
    node = _copy.deepcopy(node)
    cmp = node.test
    is_eq_call = (isinstance(cmp, ast.Compare) and len(cmp.ops) == 1
                  and isinstance(cmp.ops[0], ast.Eq) and isinstance(cmp.left, ast.Call)
                  and isinstance(cmp.left.func, ast.Name) and cmp.left.func.id == 'candidate')
    if is_eq_call and mode in ('two_sum_0', 'two_sum_1'):
        kw = {k.arg: k.value for k in cmp.left.keywords}
        target_node = kw.get('target')
        arr_node = next(v for a, v in kw.items() if a != 'target')
        _to_positional(cmp.left)
        one = 'True' if mode == 'two_sum_1' else 'False'
        return (f"assert _valid_two_sum({ast.unparse(cmp.left)}, {ast.unparse(arr_node)}, "
                f"{ast.unparse(target_node)}, {one}, {ast.unparse(cmp.comparators[0])})")
    if is_eq_call and mode in ('outer', 'outer_inner'):
        _to_positional(cmp.left)
        fn = '_eq_outer' if mode == 'outer' else '_eq_outer_inner'
        return f"assert {fn}({ast.unparse(cmp.left)}, {ast.unparse(cmp.comparators[0])})"
    return ast.unparse(_PosCall().visit(node))


def build_test_code(slug, test_src):
    tree = ast.parse(test_src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'check')
    mode = COMPARE_MODES.get(slug)
    lines = [_rewrite_assert(test_src, st, mode) for st in fn.body if isinstance(st, ast.Assert)]
    body = "# Test cases for %s\n" % slug
    if mode:
        body += COMPARE_HELPERS
    body += "_test_lines = [\n"
    for seg in lines:
        body += "    %r,\n" % seg
    body += "]\n\n"
    body += (
        "def check(candidate):\n"
        "    passed = 0\n"
        "    failed = []\n"
        "    ns = dict(globals())\n"
        "    ns['candidate'] = candidate\n"
        "    for i, line in enumerate(_test_lines, 1):\n"
        "        try:\n"
        "            exec(line, ns)\n"
        "            passed += 1\n"
        "        except AssertionError:\n"
        "            failed.append((i, line, 'wrong answer'))\n"
        "        except Exception as e:\n"
        "            failed.append((i, line, type(e).__name__ + ': ' + str(e)))\n"
        "    total = len(_test_lines)\n"
        "    print(f'{passed}/{total} tests passed')\n"
        "    if failed:\n"
        "        print(f'\\n{len(failed)} test(s) failed:')\n"
        "        for i, line, err in failed[:10]:\n"
        "            print(f'  Test #{i}: {err}')\n"
        "            print(f'    {line}')\n"
        "        if len(failed) > 10:\n"
        "            print(f'  ... and {len(failed) - 10} more')\n"
        "    else:\n"
        "        print('All tests passed! \\u2713')\n"
        "    return not failed\n"
    )
    return body


# --------------------------------------------------------------------------
# Load dataset -> per-slug prompt / entry_point / test_code, and dump test files
# --------------------------------------------------------------------------
def load_dataset():
    os.makedirs(TEST_CODE_DIR, exist_ok=True)
    out = {}
    with gzip.open(DATASET, "rt") as f:
        for line in f:
            it = json.loads(line)
            slug = it["task_id"]
            tc = build_test_code(slug, it["test"])
            out[slug] = {"prompt": it["prompt"], "entry_point": it["entry_point"], "test_code": tc}
    return out


def dump_test_files(lcd, slugs):
    """Write inspectable, runnable test files for the 150 deck problems."""
    for slug in slugs:
        if slug not in lcd:
            continue
        prompt = lcd[slug]["prompt"].replace("from sortedcontainers import SortedList", "")
        content = (
            "# Auto-generated by build_pro_deck.py — do not edit by hand.\n"
            "# Paste your Solution class where indicated, then run to self-test.\n\n"
            + prompt + "\n\n"
            "# ==== YOUR SOLUTION HERE ====\n\n\n"
            + lcd[slug]["test_code"]
            + "\ncheck(%s)\n" % lcd[slug]["entry_point"]
        )
        with open(os.path.join(TEST_CODE_DIR, slug + ".py"), "w") as f:
            f.write(content)


# --------------------------------------------------------------------------
# LeetCode question data + NeetCode solution HTML loaders (from main.ipynb)
# --------------------------------------------------------------------------
def getLeetCodeData(title_slug, isPaid=False):
    path = f"data/paidOnly/{title_slug}.json" if isPaid else f"data/leetcode-json-data/{title_slug}.json"
    with open(path) as f:
        qdata = json.load(f)
    q = qdata["data"]["question"]
    topicTagsNew, tags = {}, []
    for t in q["topicTags"]:
        tags.append(t["slug"]); topicTagsNew[t["slug"]] = t["name"]
    hints = q["hints"]; hints_html = ""
    if hints:
        hints_html = '<div class="hints-section" style="margin-top: 20px;">\n'
        for idx, hint in enumerate(hints, 1):
            hints_html += f'''<details style="margin-bottom: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
        <summary style="cursor: pointer; font-weight: bold; padding: 5px;">Hint {idx}</summary>
        <p style="margin-top: 10px; padding: 10px;">{hint}</p>
    </details>
    '''
        hints_html += "</div>"
    cs = q["codeSnippets"]
    return {
        "Id": q["questionId"], "Title": q["title"], "TitleSlug": q["titleSlug"],
        "TopicTags": json.dumps(topicTagsNew), "Difficulty": q["difficulty"],
        "Description": q["content"], "Notes": "",
        "CodeSnippets": cs[2]["code"] if cs and len(cs) > 2 and "code" in cs[2] else "",
        "Hints": hints_html, "Tags": tags,
    }


def getNeetCodeSolutionHTML(title_slug):
    removeli = '<div _ngcontent-ng-c3350875783="" class="tabs is-left" style="width: 100%; overflow-x: hidden; margin-bottom: 0px;"><ul _ngcontent-ng-c3350875783="" class="tabs-list" style="width: 100%; margin-left: 0px; margin-top: 0px; margin-bottom: 0px;"><li _ngcontent-ng-c3350875783="" class="tabs-list-item my-active-tab my-active-code-tab" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter bold-font" style="font-size: 16px;">Python</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">Java</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">C++</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">JavaScript</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">C#</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">Go</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">Kotlin</span></a></li><li _ngcontent-ng-c3350875783="" class="tabs-list-item" style="margin: 0px;"><a _ngcontent-ng-c3350875783="" role="button" tabindex="0"><span _ngcontent-ng-c3350875783="" class="tab-header font-inter light-text" style="font-size: 16px;">Swift</span></a></li><!-- --></ul></div>'
    removebtn = '<button class="copy-btn has-tooltip-left" data-tooltip="Copy"><fa-icon class="ng-fa-icon"><svg aria-hidden="true" class="svg-inline--fa fa-copy" data-icon="copy" data-prefix="fas" focusable="false" role="img" viewBox="0 0 448 512" xmlns="http://www.w3.org/2000/svg"><path d="M208 0L332.1 0c12.7 0 24.9 5.1 33.9 14.1l67.9 67.9c9 9 14.1 21.2 14.1 33.9L448 336c0 26.5-21.5 48-48 48l-192 0c-26.5 0-48-21.5-48-48l0-288c0-26.5 21.5-48 48-48zM48 128l80 0 0 64-64 0 0 256 192 0 0-32 64 0 0 48c0 26.5-21.5 48-48 48L48 512c-26.5 0-48-21.5-48-48L0 176c0-26.5 21.5-48 48-48z" fill="currentColor"></path></svg></fa-icon></button>'
    removebtn1 = removebtn.replace("viewBox", "viewbox")
    with open(f"data/neetcode-solution-html/{title_slug}.html") as f:
        html = f.read()
    html = html.replace(removeli, "").replace(removebtn, "").replace(removebtn1, "")
    html = html.replace("<!-- -->", "")
    html = html.replace('<h1 _ngcontent-ng-c3055955716="" style="font-size: 26px; margin-top: 24px; margin-bottom: 20px;">Prerequisites</h1>', "")
    html = re.sub(r"<app-prereq-cards[^>]*>.*?</app-prereq-cards>", "", html, flags=re.DOTALL)
    return html


# --------------------------------------------------------------------------
# Reuse stable model_id + deck_ids from the existing apkg so re-imports update
# instead of creating duplicate decks.
# --------------------------------------------------------------------------
def existing_ids():
    if not os.path.exists(APKG_OUT):
        return None, {}
    z = zipfile.ZipFile(APKG_OUT)
    tmp = tempfile.mkdtemp(); z.extract("collection.anki2", tmp)
    db = sqlite3.connect(os.path.join(tmp, "collection.anki2"))
    models, decks = db.execute("select models, decks from col").fetchone()
    models, decks = json.loads(models), json.loads(decks)
    mid = int(next(iter(models)))
    deck_by_name = {d["name"]: int(did) for did, d in decks.items()}
    db.close(); shutil.rmtree(tmp)
    return mid, deck_by_name


def main():
    lcd = load_dataset()
    data = json.load(open(NEETCODE_LIST))
    slugs = [urlparse(data[d][q]["url"]).path.strip("/").split("/")[-1] for d in data for q in data[d]]
    dump_test_files(lcd, slugs)

    model_id, deck_by_name = existing_ids()
    if model_id is None:
        model_id = 1189842311  # keep stable across fresh builds

    with open("card-template/front-pro.html") as f: front = f.read()
    with open("card-template/back.html") as f: back = f.read()
    with open("card-template/card.css") as f: css = f.read()

    model = genanki.Model(
        model_id=model_id, name="Basic - Anki-NeetCode - Pro",
        fields=[{"name": n} for n in ["Id", "Title", "TitleSlug", "TopicTags", "Difficulty",
                "Description", "Notes", "CodeSnippets", "Hints", "Solution", "EntryPoint", "TestCode", "Prompt"]],
        templates=[{"name": "Card 1", "qfmt": front, "afmt": back}], css=css)

    class SlugNote(genanki.Note):
        @property
        def guid(self):
            return genanki.guid_for(self.fields[2])  # stable on title slug

    decks = []
    missing = []
    for index, d in enumerate(data):
        index_str = f"0{index+1}" if index < 9 else str(index + 1)
        deck_name = f"Anki - NeetCode Pro::{index_str}. {d}"
        deck_id = deck_by_name.get(deck_name)
        if deck_id is None:
            deck_id = int(genanki.guid_for(deck_name).encode().hex()[:8], 16) | (1 << 30)
        sub = genanki.Deck(deck_id, deck_name)
        for q in data[d]:
            slug = urlparse(data[d][q]["url"]).path.strip("/").split("/")[-1]
            lc = getLeetCodeData(slug, isPaid=slug in paidOnly)
            sol = getNeetCodeSolutionHTML(slug)
            if slug not in lcd:
                missing.append(slug); prompt = entry = test_code = ""
            else:
                prompt = lcd[slug]["prompt"].replace("from sortedcontainers import SortedList", "")
                entry = lcd[slug]["entry_point"]
                test_code = lcd[slug]["test_code"]
            fields = [str(lc["Id"]), lc["Title"], lc["TitleSlug"], lc["TopicTags"], lc["Difficulty"],
                      lc["Description"], lc["Notes"], lc["CodeSnippets"], lc["Hints"], sol,
                      entry, test_code, prompt]
            sub.add_note(SlugNote(model=model, fields=fields, tags=lc["Tags"]))
        decks.append(sub)

    media_files = [
        "card-template/css/_atom-one-dark.min.css", "card-template/css/_codemirror.css",
        "card-template/css/_katex.css", "card-template/css/_nord.css",
        "card-template/js/_codemirror.js", "card-template/js/_katex.min.js",
        "card-template/js/_highlight.min.js", "card-template/js/_pyodide.js",
        "card-template/js/_python-codemirror.js", "card-template/js/_python.min.js",
        "card-template/fonts/_Material_Symbols_Outlined.woff2",
    ]
    genanki.Package(decks, media_files=media_files).write_to_file(APKG_OUT)
    print(f"WROTE {APKG_OUT}  model_id={model_id}  decks={len(decks)}")
    print(f"test files -> {TEST_CODE_DIR}/  ({len(slugs) - len(missing)} written)")
    print(f"empty tests (design/class problems, no dataset): {len(missing)} -> {missing}")


if __name__ == "__main__":
    main()
