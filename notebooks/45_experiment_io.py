"""45-only artifacts and reuse of the prior source-preservation contract."""
if not __debug__:
    raise RuntimeError('Optimized Python cannot enforce experiment invariants')
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'notebooks/data'
OUT=DATA/'45_evidence_supplement_comparison'


def digest(value):
    raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()


def read(path):return json.loads(Path(path).read_text())


def load(name, filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'notebooks'/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def write(path,value):
    path=Path(path).absolute()
    if not path.is_relative_to(OUT) or path.is_symlink():raise RuntimeError('45-only output required')
    for parent in path.parents:
        if parent==ROOT:break
        if parent.is_symlink():raise RuntimeError('Symlink output ancestor')
    if path.exists() and path.stat().st_nlink!=1:raise RuntimeError('Hardlink output forbidden')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.writing')
    if temporary.is_symlink():raise RuntimeError('Symlink temporary forbidden')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temporary.replace(path)


def freeze(name,value):
    path=OUT/name;result={**value,'sha256':digest(value)}
    if path.exists():
        if read(path)!=result:raise RuntimeError('Frozen experiment contract changed: '+name)
    else:write(path,result)
    return result


def record(path):
    stat=path.stat();h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return {'sha256':h.hexdigest(),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}


def preserve():
    prior=DATA/'43_offline_validation/protected_files.json'
    base=read(prior);expected={r['path']:{k:v for k,v in r.items() if k!='path'} for r in base['files']}
    after=base['approved_reference_change']['after']
    expected[after['path']]={k:v for k,v in after.items() if k!='path'}
    contract=OUT/'preservation_contract.json'
    if not contract.exists():
        tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        extras={name:record(ROOT/name) for name in tracked if name and name not in expected
                and not name.startswith(('notebooks/45_','notebooks/data/45_','docs/report/45_')) and (ROOT/name).is_file()}
        freeze('preservation_contract.json',{'prior_manifest':str(prior.relative_to(ROOT)),
              'prior_manifest_sha256':digest(prior.read_bytes()),'additional_files':extras})
    c=read(contract)
    if digest(prior.read_bytes())!=c['prior_manifest_sha256']:raise RuntimeError('Prior manifest changed')
    expected.update(c['additional_files'])
    changed=[name for name,r in expected.items() if not (ROOT/name).is_file() or record(ROOT/name)!=r]
    result={'passed':not changed,'checked_files':len(expected),'changed':changed}
    write(OUT/'preservation.json',result)
    if changed:raise RuntimeError('Protected input changed: '+repr(changed[:5]))
    return result
