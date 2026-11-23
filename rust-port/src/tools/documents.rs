//! Native document readers matching `webagent/documents.py`'s `read_any` return
//! shapes. Structured PDF table extraction is not available; PDF text and page
//! boundaries are preserved, as with the Python pypdf fallback.
use anyhow::{anyhow, bail, Context, Result};
use calamine::{open_workbook_auto, Reader};
use regex::Regex;
use serde_json::{json, Value};
use std::{fs, io::Read, path::Path};

/// Maximum input size accepted for in-process document parsing. Untrusted files
/// should also be processed under an OS sandbox and external memory limits.
pub const MAX_DOCUMENT_BYTES: u64 = 40 * 1024 * 1024;
pub const MAX_UNCOMPRESSED_DOCX_BYTES: u64 = 100 * 1024 * 1024;

fn supported_path(path: &Path) -> Result<String> {
    let metadata=fs::metadata(path).with_context(||format!("opening {}",path.display()))?;
    if !metadata.is_file() { bail!("not a file: {}",path.display()) }
    if metadata.len()>MAX_DOCUMENT_BYTES { bail!("document exceeds maximum accepted input size") }
    Ok(path.extension().and_then(|s|s.to_str()).unwrap_or("").to_ascii_lowercase())
}

pub fn read_any(path: impl AsRef<Path>) -> Result<Value> {
    let path=path.as_ref();
    let extension=supported_path(path)?;
    match extension.as_str() {
        "txt"|"md"|"html"|"htm"|"json"|"xml" => Ok(json!({"kind":"text","text":fs::read_to_string(path)?})),
        "csv"=>read_csv(path),
        "xlsx"|"xlsm"|"xls"|"xlsb"|"ods"=>read_spreadsheet(path),
        "pdf"=>read_pdf(path),
        "docx"=>read_docx(path),
        _=>Ok(json!({"kind":"unknown","error":format!("Unsupported file type: .{extension}")})),
    }
}

fn read_csv(path:&Path)->Result<Value>{
    let mut reader=csv::ReaderBuilder::new().has_headers(false).flexible(true).from_path(path)?;
    let mut records=Vec::new();
    for row in reader.records() {
        records.push(row?.iter().map(str::to_owned).collect::<Vec<_>>());
        if records.len()>1_000_000 { bail!("CSV row limit exceeded") }
    }
    let headers=records.first().cloned().unwrap_or_default();
    let rows=records.into_iter().skip(1).collect::<Vec<_>>();
    Ok(json!({"kind":"csv","headers":headers,"count":rows.len(),"rows":rows}))
}

fn read_spreadsheet(path:&Path)->Result<Value>{
    let mut workbook=open_workbook_auto(path)?;
    let names=workbook.sheet_names().to_vec();
    let mut sheets=serde_json::Map::new();
    for name in names {
        let range=workbook.worksheet_range(&name).with_context(||format!("reading worksheet {name}"))?;
        let mut all=Vec::new();
        for row in range.rows() {
            all.push(row.iter().map(ToString::to_string).collect::<Vec<_>>());
            if all.len()>1_000_000 { bail!("worksheet row limit exceeded: {name}") }
        }
        let headers=all.first().cloned().unwrap_or_default();
        let rows=all.into_iter().skip(1).collect::<Vec<_>>();
        sheets.insert(name,json!({"headers":headers,"count":rows.len(),"rows":rows}));
    }
    Ok(json!({"kind":"xlsx","sheets":sheets}))
}

fn read_pdf(path:&Path)->Result<Value>{
    let pages=pdf_extract::extract_text_by_pages(path).context("PDF text extraction")?;
    Ok(json!({"kind":"pdf","pages":pages.len(),"text":pages.join("\n\n"),"tables":[]}))
}

fn decode_xml(s:&str)->String{
    let re=Regex::new(r"&(#x[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);").expect("static regex");
    re.replace_all(s,|c:&regex::Captures<'_>| {
        let value=&c[1];
        let decoded=match value {"amp"=>Some('&'),"lt"=>Some('<'),"gt"=>Some('>'),"quot"=>Some('"'),"apos"=>Some('\''),
            x if x.starts_with("#x")=>u32::from_str_radix(&x[2..],16).ok().and_then(char::from_u32),
            x if x.starts_with('#')=>x[1..].parse::<u32>().ok().and_then(char::from_u32),
            _=>None};
        decoded.map(|ch|ch.to_string()).unwrap_or_else(||c[0].to_string())
    }).into_owned()
}
fn runs(xml:&str)->Result<String>{
    let text=Regex::new(r"(?s)<w:t(?:\s[^>]*)?>(.*?)</w:t>")?;
    Ok(text.captures_iter(xml).map(|c|decode_xml(&c[1])).collect::<Vec<_>>().join(""))
}
fn paragraphs(xml:&str)->Result<Vec<String>>{
    let rx=Regex::new(r"(?s)<w:p(?:\s[^>]*)?>(.*?)</w:p>")?;
    let mut out=Vec::new();for item in rx.captures_iter(xml){let content=runs(&item[1])?;if !content.trim().is_empty(){out.push(content)}}
    Ok(out)
}
fn read_docx(path:&Path)->Result<Value>{
    let f=fs::File::open(path)?;
    let mut archive=zip::ZipArchive::new(f)?;
    let mut xml=String::new();
    {
        let mut file=archive.by_name("word/document.xml").context("not a DOCX document (word/document.xml missing)")?;
        if file.size()>MAX_UNCOMPRESSED_DOCX_BYTES { bail!("DOCX uncompressed data limit exceeded") }
        file.read_to_string(&mut xml)?;
    }
    let tbl_rx=Regex::new(r"(?s)<w:tbl(?:\s[^>]*)?>(.*?)</w:tbl>")?;
    let row_rx=Regex::new(r"(?s)<w:tr(?:\s[^>]*)?>(.*?)</w:tr>")?;
    let cell_rx=Regex::new(r"(?s)<w:tc(?:\s[^>]*)?>(.*?)</w:tc>")?;
    let mut tables=Vec::new();
    for table in tbl_rx.captures_iter(&xml){
        let mut rows=Vec::new();
        for row in row_rx.captures_iter(&table[1]){
            let mut cells=Vec::new();
            for cell in cell_rx.captures_iter(&row[1]) {cells.push(paragraphs(&cell[1])?.join("\n"));}
            rows.push(cells);
        }
        tables.push(rows);
    }
    let top_level=tbl_rx.replace_all(&xml,"").into_owned();
    Ok(json!({"kind":"docx","text":paragraphs(&top_level)?.join("\n"),"tables":tables}))
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn xml_entities(){assert_eq!(decode_xml("A &amp; B &lt; 3 &#x1F600;"),"A & B < 3 😀")}
    #[test] fn paragraphs_and_runs(){
        let xml=r#"<w:document><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> world</w:t></w:r></w:p></w:document>"#;
        assert_eq!(paragraphs(xml).unwrap(),vec!["Hello world"]);
    }
    #[test] fn csv_reader_preserves_headers(){
        let dir=tempfile::tempdir().unwrap();let f=dir.path().join("test.csv");
        fs::write(&f,"a,b\nhello,world\n").unwrap();let result=read_any(&f).unwrap();
        assert_eq!(result["kind"],"csv");assert_eq!(result["count"],1);
        assert_eq!(result["headers"][0],"a");
    }
}
