data = {}
for (table of tables) {
    table0 = table
    name = table0.childNodes[0].childNodes[0].childNodes[0].childNodes[0].innerText
    table00 = table0.childNodes[0].childNodes[0].childNodes[1].getElementsByTagName("table")
    console.log(name)
    data = {...data, [name]: {}}
    row = table00[0].getElementsByTagName("tr")
    // console.log(row)
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