"""Returned workbook integrity tests; all material is synthetic."""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import json
import xml.etree.ElementTree as ET

import pytest
from fourw.review_import import import_workbook
from fourw.runs import canonical_json, RunError
from test_reviews import attestation
from fourw.packet_guard import S

FIXTURE = Path(__file__).parent / "fixtures/review"
ISSUED = FIXTURE / "export/results.xlsx"


def run_import(path, **overrides):
    public = json.loads((FIXTURE/"public-plan.json").read_bytes())
    private = json.loads((FIXTURE/"private-linkage.json").read_bytes())
    args = dict(path=path, expected_sha256=sha256(Path(path).read_bytes()).hexdigest(), issued_path=ISSUED,
        issued_sha256=sha256(ISSUED.read_bytes()).hexdigest(), public=public, private=private,
        expected_private_sha256=sha256(canonical_json(private)).hexdigest(), attestation=attestation(private))
    args.update(overrides)
    return import_workbook(**args)


def mutate(tmp_path, part, transform):
    target=tmp_path/"returned.xlsx"
    with ZipFile(ISSUED) as source, ZipFile(target,"w") as out:
        for entry in source.infolist():
            data=source.read(entry.filename)
            out.writestr(entry, transform(data) if entry.filename==part else data)
    return target


def test_blank_original_is_pending_without_any_score_or_write():
    before=ISSUED.read_bytes()
    result=run_import(ISSUED)
    assert result["status"]=="incomplete" and result["pending_count"]==4
    assert all(r["review"] is None for r in result["reviews"])
    assert result["final_adjudication"] is None and ISSUED.read_bytes()==before


@pytest.mark.parametrize("field", ["expected_sha256", "issued_sha256", "expected_private_sha256"])
def test_changed_frozen_identity_rejected(field):
    with pytest.raises(RunError):run_import(ISSUED, **{field:"0"*64})


@pytest.mark.parametrize("part,old,new,error", [
    ("xl/worksheets/sheet1.xml", b'synthetic shared text', b'changed text', "review_source_changed"),
    ("xl/worksheets/sheet1.xml", b'Review case ID', b'Changed header', "return_header_changed"),
    ("xl/worksheets/sheet2.xml", b'4W-Harmonized-v1-docs.1', b'retired-rubric', "return_instructions_changed"),
    ("xl/worksheets/sheet1.xml", b'hidden="0" customHeight', b'hidden="1" customHeight', "return_hidden_content"),
    ("xl/worksheets/sheet1.xml", b'<x:sheetData>', b'<x:sheetData><x:row r="6"/>', "return_sheet_bounds"),
    ("xl/worksheets/sheet1.xml", b'<x:sheetData>', b'<x:sheetData><x:row r="2"/>', "return_sheet_bounds"),
    ("xl/worksheets/sheet1.xml", b'<x:v>synthetic shared text</x:v>', b'<x:f>1+1</x:f><x:v>2</x:v>', "return_active_or_unapproved_content"),
    ("xl/workbook.xml", b'name="Review"', b'name="Review" state="hidden"', "return_hidden_content"),
    ("xl/worksheets/sheet1.xml", b'<x:sheetData>', b'<!-- other review --><x:sheetData>', "return_embedded_content"),
])
def test_unapproved_edits_are_not_imported(tmp_path, part, old, new, error):
    def transform(raw):
        assert old in raw
        return raw.replace(old,new,1)
    path=mutate(tmp_path,part,transform);before=path.read_bytes()
    with pytest.raises(RunError,match=error):run_import(path)
    assert path.read_bytes()==before


@pytest.mark.parametrize("part", ["xl/comments1.xml","docProps/custom.xml","xl/embeddings/label.bin","xl/externalLinks/externalLink1.xml"])
def test_extra_return_content_rejected(tmp_path,part):
    path=mutate(tmp_path,"xl/workbook.xml",lambda b:b)
    with ZipFile(path,"a") as out:out.writestr(part,b"synthetic unapproved content")
    with pytest.raises(RunError,match="return_unexpected_package_content"):run_import(path)


def test_corrupt_return_is_technical_failure(tmp_path):
    path=tmp_path/'bad.xlsx';path.write_bytes(b'broken')
    with pytest.raises(RunError,match="malformed_returned_workbook"):run_import(path)


def completed_return(tmp_path, edit=None):
    """Synthetic OOXML mutation fixture, never a delivered workbook builder."""
    def transform(raw):
        root=ET.fromstring(raw); rows=root.find(S+'sheetData')
        for row in list(rows)[1:]:
            for cell,value in zip(list(row)[2:], ['eligible',0,1,0,1,'no','Synthetic contract response only.']):
                if type(value) is str:cell.set('t','str')
                else:cell.attrib.pop('t',None)
                for child in list(cell):cell.remove(child)
                ET.SubElement(cell,S+'v').text=str(value)
        if edit:edit(root)
        return ET.tostring(root,encoding='utf-8',xml_declaration=True)
    return mutate(tmp_path,'xl/worksheets/sheet1.xml',transform)


def test_completed_values_import_without_human_finalization(tmp_path):
    path=completed_return(tmp_path);before=path.read_bytes()
    result=run_import(path)
    assert result['status']=='accepted' and result['pending_count']==0
    assert all(r['review']['scores']=={'what':0,'when':1,'where':0,'why':1} for r in result['reviews'])
    assert result['final_adjudication'] is None and result['reference_standard']=='not_established'
    assert path.read_bytes()==before


def test_one_missing_response_does_not_become_zero(tmp_path):
    def edit(root):
        cell=root.find(S+'sheetData')[1][3]
        for child in list(cell):cell.remove(child)
    result=run_import(completed_return(tmp_path,edit))
    assert result['pending_count']==1 and result['reviews'][0]['review'] is None


def test_row_reordering_retains_explicit_identity(tmp_path):
    def edit(root):
        data=root.find(S+'sheetData');reordered=[data[0],*reversed(list(data)[1:])]
        data[:]=reordered
        for index,row in enumerate(data,1):
            row.set('r',str(index))
            for cell in row:
                letters=''.join(c for c in cell.get('r') if c.isalpha())
                cell.set('r',letters+str(index))
    result=run_import(completed_return(tmp_path,edit))
    private=json.loads((FIXTURE/'private-linkage.json').read_bytes())
    assert [r['case_ref'] for r in result['reviews']]==[b['case_ref'] for b in private['bindings']]


def test_source_modified_during_import_blocks_result(tmp_path,monkeypatch):
    path=completed_return(tmp_path);digest=sha256(path.read_bytes()).hexdigest()
    original=Path.read_bytes; reads=0
    def read(p):
        nonlocal reads
        data=original(p)
        if p==path:
            reads+=1
            if reads>=2:return b'concurrent change'
        return data
    # run_import reads once to supply its default hash before the actual import.
    monkeypatch.setattr(Path,'read_bytes',read)
    public=json.loads((FIXTURE/'public-plan.json').read_bytes());private=json.loads((FIXTURE/'private-linkage.json').read_bytes())
    with pytest.raises(RunError,match='review_workbook_changed_during_import'):
        import_workbook(path,digest,ISSUED,sha256(ISSUED.read_bytes()).hexdigest(),public,private,sha256(canonical_json(private)).hexdigest(),attestation(private))


@pytest.mark.parametrize('part,old,new,error',[
    ('xl/styles.xml',b'FF202B36',b'FFFFFFFF','return_display_format_changed'),
    ('xl/worksheets/sheet1.xml',b'<x:pageMargins ',b'<x:headerFooter/><x:pageMargins ','return_active_or_unapproved_content'),
    ('xl/worksheets/sheet1.xml',b'<x:pageMargins ',b'<x:extLst/><x:pageMargins ','return_unapproved_sheet_content'),
    ('xl/worksheets/sheet1.xml',b'width="38" hidden="0"',b'width="38" hidden="1"','return_hidden_content'),
    ('xl/worksheets/sheet1.xml',b'r="A2" s="3"',b'r="J2" s="3"','return_sheet_bounds'),
    ('xl/worksheets/sheet1.xml',b'r="A2" s="3"',b'r="A2" s="3" cm="1"','return_cell_metadata'),
    ('xl/_rels/workbook.xml.rels',b'Target="/xl/styles.xml"',b'Target="http://example.invalid"','return_relationship_target'),
    ('xl/styles.xml',b'<x:fonts ',b'<x:numFmts count="1"><x:numFmt numFmtId="164" formatCode=";;;"/></x:numFmts><x:fonts ','return_unapproved_number_format'),
])
def test_display_metadata_and_links_rejected(tmp_path,part,old,new,error):
    def transform(data):
        assert old in data
        return data.replace(old,new,1)
    with pytest.raises(RunError,match=error):run_import(mutate(tmp_path,part,transform))


def test_processing_instruction_and_member_comment_rejected(tmp_path):
    path=mutate(tmp_path,'xl/workbook.xml',lambda b:b.replace(b'<x:workbook ',b'<?private label?><x:workbook ',1))
    with pytest.raises(RunError,match='return_embedded_content'):run_import(path)
    extra=tmp_path/'member-comment.xlsx'
    with ZipFile(ISSUED) as source, ZipFile(extra,'w') as out:
        for entry in source.infolist():
            entry.comment=b'unapproved metadata';out.writestr(entry,source.read(entry.filename))
    with pytest.raises(RunError,match='return_unexpected_package_content'):run_import(extra)


def test_shared_strings_cannot_hide_unreferenced_labels(tmp_path):
    def shared(raw):
        root=ET.fromstring(raw)
        ET.SubElement(ET.SubElement(root,S+'si'),S+'t').text='unreferenced label'
        return ET.tostring(root)
    with pytest.raises(RunError,match='return_unused_or_invalid_shared_strings'):
        run_import(mutate(tmp_path,'xl/sharedStrings.xml',shared))


def test_shared_string_rich_content_rejected(tmp_path):
    def shared(raw):
        root=ET.fromstring(raw);ET.SubElement(ET.SubElement(root,S+'si'),S+'r')
        return ET.tostring(root)
    with pytest.raises(RunError,match='return_unapproved_shared_string'):
        run_import(mutate(tmp_path,'xl/sharedStrings.xml',shared))


@pytest.mark.parametrize('mode,error', [('missing_data','return_sheet_structure'),('missing_row','return_sheet_bounds'),('value_attr','return_cell_metadata'),('bad_inline','return_cell_metadata'),('error_value','return_formula_or_error'),('bad_sst','return_unused_or_invalid_shared_strings'),('missing_sst_value','return_shared_string_reference')])
def test_malformed_cells_and_bounds(tmp_path,mode,error):
    def transform(raw):
        root=ET.fromstring(raw);data=root.find(S+'sheetData');cell=data[1][1]
        if mode=='missing_data':root.remove(data)
        elif mode=='missing_row':data.remove(data[-1])
        elif mode=='value_attr':cell[0].set('metadata','label')
        elif mode=='bad_inline':
            for child in list(cell):cell.remove(child)
            cell.set('t','inlineStr');ET.SubElement(cell,S+'is')
        elif mode=='error_value':cell.set('t','e');cell[0].text='#VALUE!'
        elif mode=='bad_sst':cell.set('t','s');cell[0].text='999'
        else:
            cell.set('t','s')
            for child in list(cell):cell.remove(child)
        return ET.tostring(root)
    with pytest.raises(RunError,match=error):run_import(mutate(tmp_path,'xl/worksheets/sheet1.xml',transform))


def test_explicit_empty_inline_note_preserved(tmp_path):
    def edit(root):
        cell=root.find(S+'sheetData')[1][8];cell.set('t','inlineStr')
        for child in list(cell):cell.remove(child)
        ET.SubElement(ET.SubElement(cell,S+'is'),S+'t')
    result=run_import(completed_return(tmp_path,edit))
    assert result['reviews'][0]['returned_values'][-1]==''


def test_wrong_sheet_name_is_rejected(tmp_path):
    path=mutate(tmp_path,'xl/workbook.xml',lambda b:b.replace(b'name="Review"',b'name="Other"',1))
    with pytest.raises(RunError,match='return_sheet_identity'):run_import(path)


@pytest.mark.parametrize('failure_read,error', [(2,'returned_workbook_unavailable'),(3,'review_workbook_changed_during_import')])
def test_unavailable_or_changed_input_file(tmp_path,monkeypatch,failure_read,error):
    path=completed_return(tmp_path)
    original=Path.read_bytes;returned_reads=0
    def read(p):
        nonlocal returned_reads
        if p==path:
            returned_reads+=1
            if returned_reads>=failure_read:raise OSError('synthetic read failure')
        return original(p)
    monkeypatch.setattr(Path,'read_bytes',read)
    with pytest.raises(RunError,match=error):run_import(path)


def test_issued_snapshot_changed_before_parser(monkeypatch):
    from fourw import review_import
    monkeypatch.setattr(review_import,'verify_issued_packet',lambda *args:{'workbook_sha256':'0'*64})
    with pytest.raises(RunError,match='issued_packet_identity'):run_import(ISSUED,issued_sha256='0'*64)


def test_doctype_is_not_accepted(tmp_path):
    path=mutate(tmp_path,'xl/workbook.xml',lambda b:b.replace(b'<x:workbook ',b'<!DOCTYPE workbook><x:workbook ',1))
    with pytest.raises(RunError,match='return_embedded_content'):run_import(path)


def test_empty_unicode_and_formula_like_source_survives_return_read():
    folder=Path(__file__).parent/'fixtures/review-variants'
    public=json.loads((folder/'public-plan.json').read_bytes());private=json.loads((folder/'private-linkage.json').read_bytes())
    path=folder/'results.xlsx';digest=sha256(path.read_bytes()).hexdigest()
    result=import_workbook(path,digest,path,digest,public,private,sha256(canonical_json(private)).hexdigest(),attestation(private))
    assert result['pending_count']==6
