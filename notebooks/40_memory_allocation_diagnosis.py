"""Measure allocator release without model scoring, API calls or search changes."""
import ctypes
import gc
import importlib.util
from pathlib import Path
import resource
import sys

spec=importlib.util.spec_from_file_location('allocation_recovery',Path(__file__).with_name('40_memory_recovery.py'))
R=importlib.util.module_from_spec(spec);sys.modules[spec.name]=R;spec.loader.exec_module(R)
M=R.tools();C=M.C;C.initialize(40)

def sample():
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
    return {'rss_kib':R.process_rss_kib(),'host_available_kib':available,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}

before=sample();B,x=C.experiment(40);built=sample()
gc.collect();collected=sample()
libc=ctypes.CDLL('libc.so.6');libc.malloc_trim.argtypes=[ctypes.c_size_t];libc.malloc_trim.restype=ctypes.c_int
trim_result=libc.malloc_trim(0);trimmed=sample()
result={'before':before,'factory_loaded':built,'after_gc':collected,'after_malloc_trim':trimmed,
        'malloc_trim_result':trim_result,'new_api_requests':0,'model_scoring_calls':0,
        'released_rss_kib':built['rss_kib']-trimmed['rss_kib'],'limits':'Allocator measurements only; host availability also depends on other processes; no quality or model peak proof.'}
C.write(C.STAGES[40]/'memory_allocation_diagnosis.json',result)
print(result,flush=True)
