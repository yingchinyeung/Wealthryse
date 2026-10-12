import zipfile

from ibkr_etl.presentation_text_fixes import fix_pptx, fix_xml

RPR = '<a:rPr sz="650" b="1"><a:solidFill><a:srgbClr val="002060" /></a:solidFill></a:rPr>'


def test_heading_run_split_from_the_rest_of_the_heading_is_fixed_and_keeps_formatting():
    xml = (f'<a:p><a:r>{RPR}<a:t>llustrative</a:t></a:r><a:r>{RPR}<a:t> examples only</a:t></a:r></a:p>'
           '<a:p><a:r><a:t>provided by way of example for illustrative and reference purposes</a:t></a:r></a:p>')
    fixed, heading, email = fix_xml(xml)
    assert (heading, email) == (1, 0)
    assert fixed == xml.replace('<a:t>llustrative</a:t>', '<a:t>Illustrative</a:t>')


def test_single_run_heading_and_email_are_fixed():
    xml = ('<a:r><a:t>llustrative examples only</a:t></a:r>'
           '<a:r><a:rPr sz="1200" spc="-1" /><a:t>inquiry@www.sprout-asset.com</a:t></a:r>')
    fixed, heading, email = fix_xml(xml)
    assert (heading, email) == (1, 1)
    assert '<a:t>Illustrative examples only</a:t>' in fixed
    assert '<a:rPr sz="1200" spc="-1" /><a:t>inquiry@sprout-asset.com</a:t>' in fixed


def test_other_text_is_unchanged():
    xml = '<a:r><a:t>llustrative</a:t></a:r><a:r><a:t> notes</a:t></a:r><a:r><a:t>inquiry@sprout-asset.com</a:t></a:r>'
    assert fix_xml(xml) == (xml, 0, 0)


def test_fix_pptx_rewrites_only_slide_parts(tmp_path):
    source, target = tmp_path / 'in.pptx', tmp_path / 'out.pptx'
    slide = '<p:sld><a:r><a:t>inquiry@www.sprout-asset.com</a:t></a:r></p:sld>'
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('ppt/slides/slide20.xml', slide)
        z.writestr('ppt/notesSlides/notesSlide1.xml', slide)
    receipt = fix_pptx(source, target)
    assert receipt == {'status': 'passed', 'heading_runs_fixed': 0, 'email_runs_fixed': 1}
    with zipfile.ZipFile(target) as z:
        assert 'inquiry@sprout-asset.com' in z.read('ppt/slides/slide20.xml').decode()
        assert z.read('ppt/notesSlides/notesSlide1.xml').decode() == slide
