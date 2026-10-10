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
    file=generate(stage);notebook=nbformat.read(file,as_version=4)
    commands={
      30:['python -B notebooks/30_build_structured_db.py','python -B notebooks/30_verify_preservation.py'],
      31:['python -B notebooks/31_operational_chunking.py',
          '# 새 FTS/Chroma DB 쓰기는 database-manager가 아래 모듈 함수로 수행합니다.',
          '# 31_operational_chunking.py: 각 profile의 build_fts(profile), build_vectors(profile)'],
      32:['# 기존 dataset_freeze.json은 동결된 입력입니다. 재현 시 임의 재주석하지 않습니다.',
          'python -B notebooks/32_search_experiment.py retrieval',
          'python -B notebooks/32_search_experiment.py answers',
          'python -B notebooks/32_score_answers.py --source oracle',
          'python -B notebooks/32_failure_diagnostics.py',
          'python -B notebooks/32_backend_scope_probe.py'],
      33:['python -B notebooks/33_query_routing.py',
          'python -B notebooks/32_search_experiment.py retrieval --source llm',
          'python -B notebooks/32_search_experiment.py answers --source llm',
          'python -B notebooks/32_score_answers.py --source llm',
          'python -B notebooks/32_failure_diagnostics.py --source llm',
          'python -B notebooks/33_repeat_query_plans.py',
          'python -B notebooks/30_summarize_experiments.py']}
    for cell in notebook.cells:
        if cell.cell_type=='markdown' and cell.source.startswith('## 재현과 책임'):
            cell.source=('## 재현과 책임\n\n장시간 API·모델 실행은 동일 payload 캐시·체크포인트·실패 기록을 가진 runner가 담당합니다. '
              '이 노트북의 셀은 저장된 실제 결과를 읽고 보여주며 보고서를 생성합니다. 유료 작업을 셀에서 처음부터 재실행하지 않습니다.\n\n'
              '아래 각 Python 명령 앞에 `PYTHONDONTWRITEBYTECODE=1 conda run --no-capture-output -n skn25`를 붙입니다. '
              '원본 OCR lane·코드, 동결된 JSON/SQLite·청크·새 인덱스, API 캐시와 입력 hash가 필요합니다. '
              '입력 hash가 바뀌면 캐시를 자동 덮어쓰지 않고 중단합니다. 캐시가 없는 API 실행에는 키·네트워크·비용이 필요합니다. '
              '원본 데이터와 API 키는 Git 커밋에 포함하지 않습니다. DB 쓰기는 database-manager가 담당합니다.\n\n```bash\n'+
              '\n'.join(commands[stage])+'\n```\n\n모든 결과가 완료된 뒤 `30_summarize_experiments.py`, '
              '`30_execute_experiment_notebooks.py`, `30_render_experiment_reports.py`, `30_verify_preservation.py` 순서로 최종 산출물을 확인합니다.')
    followup={
      30:"audit=C.read_json(C.STAGES[30]/'preservation_verification.json')\nprint(json.dumps(audit,ensure_ascii=False,indent=2))",
      31:"for profile in ['card_page_section_benefit','parent_child_bundle']:\n    print(profile,json.dumps(C.read_json(C.STAGES[31]/profile/'index_summary.json'),ensure_ascii=False))",
      32:"freeze=C.read_json(C.STAGES[32]/'dataset_freeze.json')\nrecords=C.read_json(C.STAGES[32]/'retrieval_results.json')['records']\nprint(json.dumps(R.aggregate(records,{q['query_id']:q for q in freeze['queries']}),ensure_ascii=False,indent=2))\nfor name in ['answer_constraint_scores.json','failure_diagnostics.json','backend_probe_results.json']:\n    data=C.read_json(C.STAGES[32]/name)\n    print(name,json.dumps(data if 'records' not in data else {'records':len(data['records'])},ensure_ascii=False)[:10000])",
      33:"plans=C.read_json(C.STAGES[33]/'parser_results.json')['records']\nfor row in plans:\n    if row['query_id'] in ['Q01','Q20','Q22']:\n        print(row['query_id'],json.dumps(row.get('plan',{'error':row.get('error')}),ensure_ascii=False,indent=2))\nprint('repeat stability',json.dumps(C.read_json(C.STAGES[33]/'repeat_results.json')['summary'],ensure_ascii=False,indent=2))"}
    marker='experiment-followup-inspection'
    existing=next((cell for cell in notebook.cells if cell.get('id')==marker),None)
    if existing is None:
        existing=nbformat.v4.new_code_cell(followup[stage]);existing.id=marker;notebook.cells.append(existing)
    else:existing.source=followup[stage]
    scope={'__name__':'__main__'};count=0
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
