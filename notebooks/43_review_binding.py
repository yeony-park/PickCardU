"""Pin finished offline inputs/reviews before any final report aggregation."""
import hashlib
import json

RECEIPT_SHA256='cd8bcecbdc8d5735d9beb2b044f6099bf9791e838ad44922bf9c0008129e6874'

def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else
        json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def verify_reviews(path,receipt_pin=RECEIPT_SHA256):
    raw=(path/'review_receipt.json').read_bytes()
    if digest(raw)!=receipt_pin:
        raise RuntimeError('Offline review receipt pin mismatch')
    receipt=json.loads(raw)
    if digest({k:v for k,v in receipt.items() if k!='sha256'})!=receipt['sha256']:
        raise RuntimeError('Offline review receipt digest mismatch')
    if receipt['source_review_complete'] is not True or receipt['old_scores_changed'] is not False or receipt['new_provider_calls']!=0:
        raise RuntimeError('Offline review qualification differs')
    for name,sha in receipt['files'].items():
        file=path/name
        if not file.is_file() or digest(file.read_bytes())!=sha:
            raise RuntimeError('Frozen review source changed: '+name)
    return receipt
