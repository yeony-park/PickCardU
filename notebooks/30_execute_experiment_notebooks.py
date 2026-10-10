"""Create and execute numbered notebook cells in skn25 without kernel side writes.

Long-running stages are resumable Python runners because model inference and
paid exact-payload caches need durable checkpoints. Notebooks inspect the same
stages, display outputs and document the complete reproduction commands.
"""
from pathlib import Path
import importlib.util
import sys
import io
import contextlib
import traceback
import nbformat

s=importlib.util.spec_from_file_location('structured_common',Path(__file__).with_name('30_structured_search_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)
NAMES={30:'structured_benefit_db',31:'operational_chunking_comparison',32:'structured_search_comparison',33:'query_routing'}
TITLES={30:'구조화 DB 생성·보존 점검',31:'현재 운영 청킹·새 검색 인덱스',32:'6조건 검색·답변 평가',33:'GPT-6 Luna/medium 질문 조건·라우팅'}

def generate(stage):
    file=C.ROOT/'notebooks'/f'{stage}_{NAMES[stage]}.ipynb'
    if file.exists():return file
    commands={30:'python -B notebooks/30_build_structured_db.py',
      31:'python -B notebooks/31_operational_chunking.py\n# database-manager executes build_fts and build_vectors in this stage',
      32:'python -B notebooks/32_search_experiment.py freeze\npython -B notebooks/32_search_experiment.py retrieval\npython -B notebooks/32_search_experiment.py answers',
      33:'python -B notebooks/33_query_routing.py\npython -B notebooks/32_search_experiment.py retrieval --source llm\npython -B notebooks/32_search_experiment.py answers --source llm'}
    setup="""from pathlib import Path
import importlib.util, sys, json
root=Path.cwd()
if root.name=='notebooks': root=root.parent
spec=importlib.util.spec_from_file_location('structured_common',root/'notebooks/30_structured_search_common.py')
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)
assert Path(sys.prefix).name=='skn25'
print({'environment':'skn25','model':C.MODEL,'reasoning':C.REASONING,'originals':'read only'})"""
    summary_files={30:['db_build_summary.json'],31:['chunking_summary.json'],32:['dataset_freeze.json','retrieval_results.json','answer_results.json'],33:['parser_results.json','retrieval_results.json','answer_results.json']}
    inspect="files="+repr(summary_files[stage])+f"\nfor name in files:\n    path=C.STAGES[{stage}]/name\n    if not path.exists(): raise RuntimeError('Required stage artifact not complete: '+name)\n    data=C.read_json(path)\n    if 'records' in data:\n        print(name,{{'records':len(data['records']),'status_counts':{{s:sum(r['status']==s for r in data['records']) for s in ['ok','failed']}}}})\n    elif 'queries' in data: print(name,{{'queries':len(data['queries']),'sha256':data['sha256']}})\n    else: print(json.dumps(data,ensure_ascii=False,indent=2)[:15000])"
    render=f"spec=importlib.util.spec_from_file_location('experiment_reports',root/'notebooks/30_render_experiment_reports.py')\nR=importlib.util.module_from_spec(spec);sys.modules[spec.name]=R;spec.loader.exec_module(R)\nprint(R.render({stage}).relative_to(root))"
    cells=[nbformat.v4.new_markdown_cell(f'# {stage}. {TITLES[stage]}\n\nOCR 통과 가정. 원본은 읽기 전용이며 현재 Upstage 계층을 수정하지 않습니다. Python 실행은 conda skn25만 허용합니다.'),
           nbformat.v4.new_markdown_cell('## 재현과 책임\n\n장시간 API·모델 실행은 체크포인트·동일 payload 캐시·실패 기록을 갖춘 아래 runner가 담당합니다. 이 노트북은 실제 저장 결과를 읽고 검증·표시하고 연결 보고서를 생성합니다. 결과를 만들어 넣지 않습니다.\n\n각 명령은 `PYTHONDONTWRITEBYTECODE=1 conda run --no-capture-output -n skn25`로 실행합니다. DB 쓰기는 database-manager가 담당하며 운영 publication runner는 실행하지 않습니다.\n\n```bash\n'+commands[stage]+'\n```'),
           nbformat.v4.new_code_cell(setup),nbformat.v4.new_code_cell(inspect),
           nbformat.v4.new_markdown_cell('## 해석 범위\n\nsource-relative raw OCR 필드 라벨과 새로운 개발/확증 cohort입니다. 독립 PDF 검증, 전수 정답 recall, 운영 채택을 뜻하지 않습니다. 프로필 비교는 청킹·재정렬·근거 구성 전체의 비교입니다. 완전한 재현은 입력 hash·원본 코드 hash·새 인덱스·저장 API 응답과 보고서의 실패 분모를 함께 확인해야 합니다.'),
           nbformat.v4.new_code_cell(render)]
    notebook=nbformat.v4.new_notebook(cells=cells,metadata={'kernelspec':{'display_name':'Python (skn25)','language':'python','name':'python3'},'language_info':{'name':'python','version':sys.version.split()[0]}})
    nbformat.write(notebook,file);return file

def execute(stage):
    file=generate(stage);notebook=nbformat.read(file,as_version=4);scope={'__name__':'__main__'};count=0
    for cell in notebook.cells:
        if cell.cell_type!='code':continue
        count+=1;cell.execution_count=count;cell.outputs=[];buffer=io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(buffer):exec(compile(cell.source,str(file),'exec'),scope)
        except Exception as exc:
            cell.outputs.append(nbformat.v4.new_output('error',ename=type(exc).__name__,evalue=str(exc),traceback=traceback.format_exc().splitlines()))
            nbformat.write(notebook,file);raise
        cell.outputs.append(nbformat.v4.new_output('stream',name='stdout',text=buffer.getvalue()))
    nbformat.validate(notebook);nbformat.write(notebook,file);print('executed',file.name,count,'code cells',flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=int,choices=list(NAMES));parser.add_argument('--generate-only',action='store_true');args=parser.parse_args()
    for stage in [args.stage] if args.stage else NAMES:
        print(generate(stage)) if args.generate_only else execute(stage)
