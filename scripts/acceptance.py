#!/usr/bin/env python3
"""Independent API acceptance using the real pinned CLI and an isolated runtime."""
from __future__ import annotations
import csv
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8051'
D = Decimal


def request(path, data=None, expected=200, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    req = Request(URL + '/api' + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        response = urlopen(req, timeout=65)
    except HTTPError as exc:
        response = exc
    content = response.read()
    assert response.code == expected, (path, response.code, content[:500])
    return content if raw else json.loads(content)


def launch(runtime, log):
    env = {**os.environ, 'FACTOR_LAB_RUNTIME_DIR': str(runtime)}
    process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'app.main:app', '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1', '--port', '8051'], cwd=ROOT, env=env, stdout=log, stderr=log)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Acceptance API exited; inspect .runtime/acceptance.log')
        try:
            assert request('/health')['engine_ready']
            return process
        except (URLError, TimeoutError):
            time.sleep(.1)
    process.terminate()
    raise RuntimeError('Acceptance API did not start')


def metrics(valuation):
    for key in ('base', 'stress'):
        assert D(valuation['portfolio'][key]) == sum(D(r['metrics'][key]) for r in valuation['deals'])
    assert D(valuation['portfolio']['dnpv']) == D(valuation['portfolio']['stress']) - D(valuation['portfolio']['base'])
    for result in valuation['deals']:
        assert D(result['flows'][-1]['remaining_principal']) == 0
        assert all(D(flow['df_base']).is_finite() and D(flow['df_stress']).is_finite() for flow in result['flows'])


def main():
    (ROOT / '.runtime').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='acceptance-', dir=ROOT / '.runtime') as temp, (ROOT / '.runtime/acceptance.log').open('w') as log:
        runtime = Path(temp)
        process = launch(runtime, log)
        try:
            catalog = request('/deals')
            assert len(catalog) == 55
            assert {x['id'] for x in catalog if x['eligible']} == {str(i) for i in [*range(1, 15), 21, 26, 27, 28, 29]}
            request('/deals', {'name': 'Invalid', 'current_principal_amt': '1', 'interest_rate': '.1', 'maturity_dttm': 'bad', 'discriminator': 'ANN'}, expected=422)
            new = request('/deals', {'name': 'Проверка учебного шаблона', 'current_principal_amt': '100000', 'interest_rate': '.1', 'maturity_dttm': '2024-08-31', 'discriminator': 'SIMPLE'}, expected=201)
            assert new['scheme_editable'] and new['day_convention_cd'] == 'ACT/365'
            profiles = request('/curve-profiles')
            assert len(profiles) == 2
            payload = {'deal_ids': ['1', '2', '28'], 'profile_id': 'repo-up100-v1', 'act_act_as_365': False}
            value = request('/valuations', payload)
            metrics(value)
            # Independent previously reviewed reference on the locked CLI.
            first = next(r for r in value['deals'] if r['deal_id'] == '1')['metrics']
            assert first == {'base': '3173152.17', 'stress': '2919001.92', 'dnpv': '-254150.25'}, first
            other = request('/valuations', {**payload, 'profile_id': 'flat-10-12-v1'})
            for a, b in zip(value['deals'], other['deals']):
                assert [{k: f[k] for k in ('date', 'principal', 'interest', 'total', 'remaining_principal')} for f in a['flows']] == [{k: f[k] for k in ('date', 'principal', 'interest', 'total', 'remaining_principal')} for f in b['flows']]
            ex_payload = {**payload, 'target_deal_id': '1', 'factor': 'amount', 'values': ['1', '1.1']}
            experiment = request('/experiments', ex_payload)
            assert len(experiment['variants']) == 2
            assert all(D(n) == 0 for n in experiment['variants'][0]['effect'].values())
            for variant in experiment['variants']:
                metrics(variant['valuation'])
                assert D(variant['effect']['dnpv']) == D(variant['effect']['stress']) - D(variant['effect']['base'])
                assert variant['effect'] == variant['target_effect']
                for ident in ('2', '28'):
                    assert next(r for r in experiment['original']['deals'] if r['deal_id'] == ident) == next(r for r in variant['valuation']['deals'] if r['deal_id'] == ident)
            scaled = experiment['variants'][1]
            assert next(r for r in scaled['valuation']['deals'] if r['deal_id'] == '1')['metrics'] == {'base': '3490467.39', 'stress': '3210902.11', 'dnpv': '-279565.28'}
            assert scaled['effect']['dnpv'] == '-25415.03'
            for factor, values in [('rate', ['.075', '.085']), ('term', ['12', '24']), ('scheme', ['ANN', 'RGM', 'SIMPLE'])]:
                run = request('/experiments', {**payload, 'target_deal_id': '28', 'factor': factor, 'values': values})
                for variant in run['variants']:
                    metrics(variant['valuation'])
            for ident, factor, value in [('1', 'day_count', 'ACT365'),
                                         ('28', 'payment_day', '25'),
                                         ('21', 'payment_frequency', '3M')]:
                assert factor in next(row for row in catalog if row['id'] == ident)['available_factors']
                run = request('/experiments', {**payload, 'deal_ids': [ident],
                              'target_deal_id': ident, 'factor': factor, 'values': [value]})
                original, changed = run['original'], run['variants'][0]['valuation']
                metrics(changed)
                assert original['profile'] == changed['profile']
                assert original['snapshot'][0]['interest_rate'] == changed['snapshot'][0]['interest_rate']
                flows_before, flows_after = original['deals'][0]['flows'], changed['deals'][0]['flows']
                if factor == 'day_count':
                    assert [f['interest'] for f in flows_before] != [f['interest'] for f in flows_after]
                elif factor == 'payment_day':
                    assert [f['date'] for f in flows_before] != [f['date'] for f in flows_after]
                else:
                    assert len(flows_after) < len(flows_before)
                assert D(run['variants'][0]['effect']['dnpv']) != 0
            # The separate two-factor path must not change or add archive entries.
            before_interaction = request('/experiments')
            paired = {**payload, 'deal_ids': ['28'], 'target_deal_id': '28',
                      'factor_a': 'term', 'base_value_a': '12', 'value_a': '36',
                      'factor_b': 'scheme', 'base_value_b': 'ANN', 'value_b': 'SIMPLE'}
            interaction = request('/interactions', paired)
            reference = json.loads((ROOT / 'backend/tests/fixtures/interaction_reference.json').read_text())
            for state, reference_key in [('original', '12_ANN'), ('a', '36_ANN'),
                                         ('b', '12_SIMPLE'), ('both', '36_SIMPLE')]:
                valuation = interaction['states'][state]
                metrics(valuation)
                assert valuation['portfolio'] == reference['states'][reference_key]['metrics']
                assert valuation['profile']['id'] == payload['profile_id']
            for metric in ('base', 'stress', 'dnpv'):
                result = interaction['effects']
                assert D(result['interaction'][metric]) == D(reference['effects'][metric]['interaction'])
                assert D(result['both'][metric]) == sum(D(result[k][metric]) for k in ('a', 'b', 'interaction'))
            request('/interactions', {**paired, 'factor_b': 'term', 'value_b': '24', 'base_value_b': '12'}, expected=422)
            assert request('/experiments') == before_interaction
            discount_input = {**payload, 'deal_ids': ['28'], 'values': ['0', '100']}
            for factor in ('parallel_shift', 'stress_shift', 'long_end_shift'):
                discount = request('/discount-experiments', {**discount_input, 'factor': factor})
                original = discount['original']
                unchanged, shifted = discount['variants']
                assert all(D(value) == 0 for value in unchanged['effect'].values())
                metrics(shifted['valuation'])
                assert shifted['valuation']['snapshot'] == original['snapshot']
                assert shifted['valuation']['profile']['compounding'] == original['profile']['compounding']
                assert D(shifted['effect']['stress']) < 0
                if factor == 'stress_shift':
                    assert D(shifted['effect']['base']) == 0
                    assert D(shifted['effect']['dnpv']) == D(shifted['effect']['stress'])
                else:
                    assert D(shifted['effect']['base']) < 0
                fields = ('date', 'principal', 'interest', 'total', 'remaining_principal')
                before = [tuple(f[key] for key in fields) for f in original['deals'][0]['flows']]
                after = [tuple(f[key] for key in fields) for f in shifted['valuation']['deals'][0]['flows']]
                assert before == after
            for values in (['501'], ['0', '-0'], ['NaN']):
                request('/discount-experiments', {**discount_input, 'factor': 'parallel_shift', 'values': values}, expected=422)
            assert request('/experiments') == before_interaction
            accrued = request('/experiments', {**payload, 'deal_ids': [new['id']],
                              'target_deal_id': new['id'], 'factor': 'accrued_interest', 'values': ['0', '1000']})
            initial_flow = accrued['original']['deals'][0]['flows'][0]
            changed_flow = accrued['variants'][1]['valuation']['deals'][0]['flows'][0]
            assert D(changed_flow['interest']) - D(initial_flow['interest']) == 1000
            assert accrued['variants'][1]['valuation']['profile'] == accrued['original']['profile']
            assert all(D(v) == 0 for v in accrued['variants'][0]['effect'].values())
            series = request('/experiments', {**payload, 'deal_ids': ['28'], 'target_deal_id': '28', 'factor': 'amount', 'values': [str(D('.8') + D('.02') * i) for i in range(21)]})
            assert len(series['variants']) == 21
            request('/experiments', {**ex_payload, 'values': [str(i + 1) for i in range(22)]}, expected=422)
            for bad in ('NaN', 'Infinity', '-1', 'nonsense'):
                request('/experiments', {**ex_payload, 'values': [bad]}, expected=422)
            request('/valuations', {**payload, 'deal_ids': ['1', '1']}, expected=422)
            request('/valuations', {**payload, 'deal_ids': ['15']}, expected=422)
            request('/valuations', {**payload, 'profile_id': 'missing'}, expected=404)
            request('/experiments/no-such-experiment', expected=404)
            content = request('/experiments/' + experiment['id'] + '/export.csv', raw=True)
            assert content.startswith(b'\xef\xbb\xbf')
            assert len(list(csv.reader(io.StringIO(content.decode('utf-8-sig')), delimiter=';'))) > 5
            assert request('/experiments/' + experiment['id']) == experiment
            history = request('/experiments')
            assert len(history) == 9
            process.terminate()
            process.wait(timeout=10)
            process = launch(runtime, log)
            assert request('/experiments/' + experiment['id']) == experiment
            assert len(request('/deals')) == 56
            print('PASS: реальный CLI; однофакторные серии, взаимодействие, три сдвига кривых, неизменность платежей, начисленные проценты, CSV и история после перезапуска.')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
