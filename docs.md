# Create your own deck

## Installation

### Prerequisites

```bash
# Python 3.8+
python --version

# Install required packages
pip install requests beautifulsoup4 selenium genanki
```

### Chrome WebDriver

Selenium requires ChromeDriver for web scraping.

## Usage

### 1. Extract NeetCode 150 List

1. Open https://neetcode.io/practice in your browser
2. Expand all question tables
3. Open DevTools Console and run:

```javascript
data = {}
for (table of tables) {
    table0 = table
    name = table0.childNodes[0].childNodes[0].childNodes[0].childNodes[0].innerText
    table00 = table0.childNodes[0].childNodes[0].childNodes[1].getElementsByTagName("table")
    console.log(name)
    data = {...data, [name]: {}}
    row = table00[0].getElementsByTagName("tr")
    for (tr of row) {
        if (!tr.childNodes[2].childNodes[1]) {
            continue
        }
        qname = tr.childNodes[2].innerText.trim()
        nurl = tr.childNodes[2].childNodes[0].href
        url = tr.childNodes[2].childNodes[1].href
        difficulty = tr.childNodes[3].innerText
        console.log(qname, nurl, url, difficulty)
        data[name][qname] = {nurl: nurl, url: url, difficulty: difficulty}
    }    
}

let a = document.createElement('a');
a.href = "data:application/octet-stream,"+encodeURIComponent(JSON.stringify(data));
a.download = 'neetcode-150-list.json';
a.click();
```

4. Save the downloaded `neetcode-150-list.json` to the project root

### 2. Fetch LeetCode Questions

Run the notebook cells to fetch question data from LeetCode's GraphQL API:

```python
# Extract all questions
for d in data:
    questions = data[d]
    for q in questions:
        url = questions[q]['url'].replace("problems", "graphql")
        get_q_json(url)
```

### 3. Fetch NeetCode Solutions

Scrape solution HTML from NeetCode using Selenium:

```python
driver = webdriver.Chrome()
for d in data:
    questions = data[d]
    for q in questions:
        nurl = questions[q]['nurl'].replace('?list=neetcode150', '/solution')
        get_solution_html(nurl, title_slug, driver)
driver.quit()
```

### 4. Get Premium Questions

Premium question descriptions are sourced from:
[Complete-LeetCode-Premium-Problems](https://github.com/AkashSingh3031/Complete-LeetCode-Premium-Problems)

Place the markdown files in `data/paidOnly/` directory.

### 5. Generate Anki Deck

Run the final cell to create `Anki-NeetCode.apkg`:

```python
# Creates organized subdecks
genanki.Package(decks).write_to_file('Anki-NeetCode.apkg')
```
