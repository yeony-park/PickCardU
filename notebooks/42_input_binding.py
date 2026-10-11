"""Pin the pre-answer freeze receipt without exposing labels to retrieval."""
import json
import hashlib

RECEIPT_SHA256 = '1e04947d34b92bf06d5d16c683bc328a8b0de1787b60aa061c077335183c159f'

def verify_inputs(path, receipt_pin=RECEIPT_SHA256):
    def digest(value):
        return hashlib.sha256(value if isinstance(value, bytes) else
            json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    receipt_path = path / 'freeze_receipt.json'
    if digest(receipt_path.read_bytes()) != receipt_pin:
        raise RuntimeError('Frozen receipt pin mismatch')
    values = {name: json.loads((path / (name + '.json')).read_text()) for name in
              ['dataset','questions','protocol','catalog','freeze_receipt']}
    for name, value in values.items():
        if digest({k:v for k,v in value.items() if k != 'sha256'}) != value['sha256']:
            raise RuntimeError('Frozen artifact digest mismatch: ' + name)
    receipt = values['freeze_receipt']
    if receipt['paid_api_files_at_freeze'] != 0:
        raise RuntimeError('Input labels were not frozen before paid execution')
    for name in ['dataset','questions','protocol']:
        if values[name]['sha256'] != receipt[name + '_sha256']:
            raise RuntimeError(name + ' differs from frozen receipt')
    dataset, questions, protocol, catalog = (values[name] for name in ['dataset','questions','protocol','catalog'])
    if dataset['questions_sha256'] != questions['sha256'] or any(value['protocol_sha256'] != protocol['sha256'] for value in [dataset,questions,catalog]):
        raise RuntimeError('Dataset/public/protocol binding mismatch')
    if questions['catalog_sha256'] != catalog['sha256']:
        raise RuntimeError('Public catalog binding mismatch')
    if protocol.get('source_relation_review_sha256'):
        if digest((path / 'source_relation_review.json').read_bytes()) != protocol['source_relation_review_sha256']:
            raise RuntimeError('Source relation review changed')
    return {'questions':questions,'protocol':protocol,'catalog':catalog,
            'dataset_sha256':dataset['sha256'],'receipt_sha256':receipt_pin}
