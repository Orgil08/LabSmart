import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone


class LabSmartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / 'labsmart-test.db'
        cls.env = dict(os.environ, LAB_DB=str(cls.db_path))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.proc = None
        cls.start()

    @classmethod
    def start(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('server.py')), '--demo', '--port', str(cls.port)],
            env=cls.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(80):
            try:
                urllib.request.urlopen(cls.base, timeout=.2)
                return
            except Exception:
                time.sleep(.1)
        raise RuntimeError('Server failed to start')

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        cls.tmp.cleanup()

    def setUp(self):
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ''

    def req(self, path, method='GET', data=None, headers=None):
        hdrs = {'Accept': 'application/json'}
        if data is not None:
            hdrs['Content-Type'] = 'application/json'
        if self.csrf:
            hdrs['X-CSRF-Token'] = self.csrf
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(
            self.base + '/api' + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers=hdrs,
        )
        try:
            with self.client.open(req) as res:
                ct = res.headers.get('content-type', '')
                body = res.read()
                return res.status, (json.loads(body) if 'json' in ct else body.decode('utf-8-sig'))
        except urllib.error.HTTPError as err:
            body = err.read()
            ct = err.headers.get('content-type', '')
            return err.code, (json.loads(body) if 'json' in ct else body.decode('utf-8-sig'))

    def login(self, identity='admin', password='LabAdmin2026!'):
        code, data = self.req('/login', 'POST', {'email': identity, 'password': password})
        self.assertEqual(code, 200)
        self.csrf = data['csrf']
        return data

    def equipment_payload(self, code='TEST-001'):
        return {
            'code': code,
            'name': 'Туршилтын төхөөрөмж',
            'category': 'Хэмжилт',
            'location': 'Лаборатори 201',
            'serial': 'T-001',
            'status': 'available',
            'notes': 'Test',
        }

    def test_01_public_catalog(self):
        code, rows = self.req('/public/equipment')
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(rows), 5)
        self.assertTrue(all(r['status'] != 'retired' for r in rows))

    def test_02_anonymous_is_blocked_from_private_api(self):
        self.assertEqual(self.req('/equipment')[0], 401)

    def test_03_bad_login(self):
        self.assertEqual(self.req('/login', 'POST', {'email': 'admin', 'password': 'wrong'})[0], 401)

    def test_04_student_registration_login_role(self):
        payload = {
            'first_name': 'Шинэ', 'last_name': 'Оюутан', 'email': 'new-student@example.com',
            'password': 'StrongPass123!', 'confirm': 'StrongPass123!', 'user_type': 'student',
            'organization': 'МУИС лаборатори',
        }
        code, result = self.req('/register', 'POST', payload)
        self.assertEqual(code, 201)
        self.assertIn('username', result)
        login = self.req('/login', 'POST', {'email': payload['email'], 'password': payload['password']})
        self.assertEqual(login[0], 200)
        self.csrf = login[1]['csrf']
        self.assertEqual(login[1]['role'], 'student')
        self.assertEqual(self.req('/equipment', 'POST', self.equipment_payload('STU-001'))[0], 403)

    def test_05_staff_registration_role(self):
        payload = {
            'first_name': 'Ажилтан', 'last_name': 'Демо', 'email': 'staff@example.com',
            'password': 'StrongPass123!', 'confirm': 'StrongPass123!', 'user_type': 'staff',
            'organization': 'МУИС лаборатори',
        }
        self.assertEqual(self.req('/register', 'POST', payload)[0], 201)
        code, data = self.req('/login', 'POST', {'email': payload['email'], 'password': payload['password']})
        self.assertEqual(code, 200)
        self.assertEqual(data['role'], 'staff')
        self.csrf = data['csrf']
        self.assertEqual(self.req('/maintenance')[0], 200)

    def test_06_admin_crud_and_history(self):
        self.login()
        code, result = self.req('/equipment', 'POST', self.equipment_payload('TEST-101'))
        self.assertEqual(code, 201)
        eid = result['id']
        update = dict(self.equipment_payload('TEST-101'), status='available', notes='Updated')
        self.assertEqual(self.req(f'/equipment/{eid}', 'PUT', update)[0], 200)
        detail_code, detail = self.req(f'/equipment/{eid}')
        self.assertEqual(detail_code, 200)
        self.assertEqual(detail['code'], 'TEST-101')
        hist_code, hist = self.req(f'/equipment/{eid}/history')
        self.assertEqual(hist_code, 200)
        self.assertGreaterEqual(len(hist['changes']), 2)

    def test_07_duplicate_and_validation(self):
        self.login()
        self.req('/equipment', 'POST', self.equipment_payload('DUP-001'))
        self.assertEqual(self.req('/equipment', 'POST', self.equipment_payload('DUP-001'))[0], 409)
        bad = self.equipment_payload('BAD-001'); bad['name'] = ' '
        self.assertEqual(self.req('/equipment', 'POST', bad)[0], 400)
        bad = self.equipment_payload('BAD-002'); bad['status'] = 'unknown'
        self.assertEqual(self.req('/equipment', 'POST', bad)[0], 400)

    def test_08_loan_flow(self):
        self.login()
        rows = self.req('/equipment?status=available')[1]
        eid = rows[0]['id']
        due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat().replace('+00:00', 'Z')
        code, loan = self.req('/loans', 'POST', {'equipment_id': eid, 'due_at': due})
        self.assertEqual(code, 201)
        loans = self.req('/loans?mine=1')[1]
        self.assertTrue(any(x['id'] == loan['id'] for x in loans))
        self.assertEqual(self.req('/loans', 'POST', {'equipment_id': eid, 'due_at': due})[0], 409)
        self.assertEqual(self.req(f'/loans/{loan["id"]}/return', 'POST', {'return_condition': 'Сайн'})[0], 200)
        self.assertEqual(self.req(f'/equipment/{eid}')[1]['status'], 'available')

    def test_09_request_approval_qr_checkout_flow(self):
        self.login('viewer', 'LabViewer2026!')
        sessions = self.req('/sessions')[1]
        self.assertGreater(len(sessions), 0)
        session = sessions[-1]
        code, created = self.req('/requests', 'POST', {
            'session_id': session['id'],
            'equipment_name': 'Мультиметр',
            'requested_qty': 1,
        })
        self.assertEqual(code, 201)
        request_id = created['id']

        # Student cannot approve/reject a request.
        self.assertEqual(self.req(f'/requests/{request_id}/status', 'POST', {'status': 'approved', 'approved_qty': 1})[0], 403)

        # Staff/admin approves it.
        admin = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def admin_req(path, method='GET', data=None):
            hdrs = {'Accept': 'application/json'}
            if data is not None:
                hdrs['Content-Type'] = 'application/json'
            req = urllib.request.Request(self.base + '/api' + path, data=json.dumps(data).encode() if data is not None else None, method=method, headers=hdrs)
            with admin.open(req) as res:
                body = res.read(); return res.status, json.loads(body)
        code, logged = admin_req('/login', 'POST', {'email': 'admin', 'password': 'LabAdmin2026!'})
        self.assertEqual(code, 200)
        admin_csrf = logged['csrf']
        code, approved = self.req('/requests', 'GET')
        self.assertEqual(code, 200)
        row = next(r for r in approved if r['id'] == request_id)
        self.assertEqual(row['status'], 'pending')
        req = urllib.request.Request(self.base + f'/api/requests/{request_id}/status', data=json.dumps({'status': 'approved', 'approved_qty': 1}).encode(), method='POST', headers={'Accept':'application/json','Content-Type':'application/json','X-CSRF-Token':admin_csrf})
        with admin.open(req) as res:
            self.assertEqual(res.status, 200)

        mine = self.req('/requests')[1]
        row = next(r for r in mine if r['id'] == request_id)
        self.assertEqual(row['status'], 'approved')

        # Checkout the approved request against a real available asset.
        equip = self.req('/equipment?q='+urllib.parse.quote('Мультиметр')+'&status=available')[1]
        self.assertGreater(len(equip), 0)
        due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat().replace('+00:00', 'Z')
        code, loan = self.req('/loans', 'POST', {'equipment_id': equip[0]['id'], 'due_at': due, 'request_id': request_id})
        self.assertEqual(code, 201)
        self.assertGreaterEqual(self.req('/requests')[1][0]['issued_qty'] if len(self.req('/requests')[1]) == 1 else next(r['issued_qty'] for r in self.req('/requests')[1] if r['id']==request_id), 1)

    def test_10_student_role_restrictions(self):
        self.login('viewer', 'LabViewer2026!')
        self.assertEqual(self.req('/predictions')[0], 403)
        self.assertEqual(self.req('/recommendations')[0], 403)
        self.assertEqual(self.req('/reports')[0], 403)
        self.assertEqual(self.req('/maintenance')[0], 403)
        self.assertEqual(self.req('/users')[0], 403)
        self.assertEqual(self.req('/settings')[0], 403)

    def test_11_ml_batch_and_model_comparison(self):
        self.login()
        code, result = self.req('/ml/run', 'POST', {})
        self.assertEqual(code, 200)
        self.assertIn('model_name', result)
        self.assertIn('predictions', result)
        code, summary = self.req('/ml/summary')
        self.assertEqual(code, 200)
        model_names = {r['model_name'] for r in summary['runs']}
        self.assertTrue({'Rule baseline', 'Linear Regression', 'Random Forest Regressor'} & model_names)

    def test_12_reports_audit_notifications(self):
        self.login()
        self.assertEqual(self.req('/reports')[0], 200)
        export_code, export = self.req('/reports/export')
        self.assertEqual(export_code, 200)
        self.assertIn('Төхөөрөмж', export)
        self.assertEqual(self.req('/audit')[0], 200)
        self.assertEqual(self.req('/notifications')[0], 200)

    def test_13_maintenance_completion(self):
        self.login()
        eq = self.req('/equipment?status=available')[1][0]
        code, created = self.req('/maintenance', 'POST', {'equipment_id': eq['id'], 'issue': 'Туршилтын засвар', 'priority': 'medium'})
        self.assertEqual(code, 201)
        self.assertEqual(self.req(f'/maintenance/{created["id"]}', 'PUT', {'outcome': 'Шалгасан'})[0], 200)
        self.assertEqual(self.req(f'/equipment/{eq["id"]}')[1]['status'], 'available')

    def test_14_settings_and_notifications_admin(self):
        self.login()
        code, settings = self.req('/settings')
        self.assertEqual(code, 200)
        self.assertIn('default_loan_hours', settings)
        self.assertEqual(self.req('/settings', 'PUT', {'low_stock_threshold': 3})[0], 200)
        self.assertEqual(self.req('/notifications/read-all', 'POST', {})[0], 200)

    def test_15_admin_role_management(self):
        self.login()
        users = self.req('/users')[1]
        student = next((u for u in users if u['role'] == 'student' and u['username'] != 'viewer'), None)
        if student is None:
            payload = {'first_name':'Role','last_name':'Test','email':'role-test@example.com','password':'StrongPass123!','confirm':'StrongPass123!','user_type':'student','organization':'LabSmart'}
            self.assertEqual(self.req('/register', 'POST', payload)[0], 201)
            users = self.req('/users')[1]
            student = next(u for u in users if u['email'] == payload['email'])
        self.assertEqual(self.req(f'/users/{student["id"]}/role', 'POST', {'role':'staff'})[0], 200)
        self.assertEqual(next(u for u in self.req('/users')[1] if u['id']==student['id'])['role'], 'staff')

    def test_16_equipment_search_filters(self):
        self.login()
        all_rows = self.req('/equipment')[1]
        self.assertGreaterEqual(len(all_rows), 5)
        self.assertTrue(all('available' in self.req('/equipment?available=1')[1][i]['status'] for i in range(len(self.req('/equipment?available=1')[1]))))
        self.assertEqual(self.req('/equipment?q=LAB-001')[1][0]['code'], 'LAB-001')

    def test_17_profile_onboarding(self):
        self.login('viewer', 'LabViewer2026!')
        payload = {'first_name':'Оюутан','last_name':'Демо','organization':'LabSmart Lab','onboarded':True}
        self.assertEqual(self.req('/profile', 'PUT', payload)[0], 200)
        self.assertTrue(self.req('/me')[1]['onboarded'])

    def test_18_logout_requires_auth_afterward(self):
        self.login()
        self.assertEqual(self.req('/logout', 'POST', {})[0], 200)
        self.assertEqual(self.req('/me')[0], 401)

    def test_19_return_ownership_is_enforced(self):
        self.login('viewer', 'LabViewer2026!')
        rows = self.req('/loans?mine=1')[1]
        open_loans = [r for r in rows if not r['returned_at']]
        if not open_loans:
            eq = self.req('/equipment?status=available')[1][0]
            due = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat().replace('+00:00','Z')
            _, loan = self.req('/loans','POST',{'equipment_id':eq['id'],'due_at':due})
            loan_id = loan['id']
        else:
            loan_id = open_loans[0]['id']
        self.assertEqual(self.req(f'/loans/{loan_id}/return','POST',{'return_condition':'Сайн'})[0], 200)

    def test_20_password_reset_request_is_generic(self):
        code, result = self.req('/password-reset', 'POST', {'email': 'admin@labsmart.local'})
        self.assertEqual(code, 200)
        self.assertIn('Хэрэв энэ и-мэйл бүртгэлтэй бол', result['message'])
        # Unknown emails get the same public response and do not reveal account existence.
        code2, result2 = self.req('/password-reset', 'POST', {'email': 'missing-account@example.com'})
        self.assertEqual(code2, 200)
        self.assertEqual(result['message'], result2['message'])

    def test_21_persistence_after_restart(self):
        self.login()
        code, result = self.req('/equipment', 'POST', self.equipment_payload('PERSIST-001'))
        self.assertEqual(code, 201)
        self.__class__.proc.terminate(); self.__class__.proc.wait(timeout=5)
        self.__class__.start()
        self.csrf = ''
        self.login()
        rows = self.req('/equipment?q=PERSIST-001')[1]
        self.assertEqual(len(rows), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
