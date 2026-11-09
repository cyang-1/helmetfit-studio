import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from studio import lux
from studio.prompts import MAX_THEME, PRESETS, STRUCTURES, make_spec


class HelmetPromptTests(unittest.TestCase):
    def test_templates_use_distinct_directions_and_keep_the_theme(self):
        for key, style in [('character','cartoon'), ('mecha','cyberpunk'), ('creature','fantasy')]:
            with self.subTest(preset=key):
                spec = make_spec('  月光守望者  ', key)
                self.assertEqual(spec['theme'],'月光守望者')
                self.assertEqual(spec['preset'],key)
                self.assertEqual(spec['style'],style)
                self.assertIn('月光守望者',spec['prompt'])
                self.assertIn('可穿戴',spec['prompt'])
                self.assertIn('入口',spec['prompt'])
                self.assertNotIn('红色软帽',spec['prompt'])
        self.assertEqual(set(PRESETS),{'character','mecha','creature'})
        self.assertEqual(len({make_spec('月兔', k)['prompt'] for k in PRESETS}),3)

    def test_santa_has_a_connected_crown_and_beard(self):
        for name in ('圣诞老人','Santa Claus','Father Christmas'):
            prompt = make_spec(name)['prompt']
            self.assertIn('帽尖回贴帽身',prompt)
            self.assertIn('胡须塑成宽厚的连续浮雕',prompt)
            self.assertIn('帽檐以下的左右耳部、太阳穴侧后方和整个后脑向外敞开',prompt)
            self.assertIn('红色只用于帽线以上的帽身',prompt)
            self.assertNotIn('外壳从盔顶连续包覆到左右耳部和后脑',prompt)

    def test_construction_is_independent_of_visual_style(self):
        self.assertEqual(set(STRUCTURES), {'auto','cap_mask','full_shell'})
        for preset in PRESETS:
            with self.subTest(preset=preset):
                auto = make_spec('圣诞老人',preset)
                closed = make_spec('圣诞老人',preset,'full_shell')
                custom_open = make_spec('装甲机器人',preset,'cap_mask')
                self.assertEqual((auto['structure'],auto['resolved_structure']), ('auto','cap_mask'))
                self.assertEqual((closed['structure'],closed['resolved_structure']), ('full_shell','full_shell'))
                self.assertEqual(custom_open['resolved_structure'],'cap_mask')
                self.assertEqual(closed['style'],auto['style'])
                self.assertIn('后脑壳',closed['prompt'])
                self.assertNotIn('胡须只在脸前',closed['prompt'])

    def test_auto_prefers_explicit_construction_then_subject_then_preset(self):
        cases = [
            ('圣诞老人，全包覆', 'character', 'full_shell'),
            ('深海机甲，不包住后脑', 'mecha', 'cap_mask'),
            ('狼王，帽子＋脸', 'creature', 'cap_mask'),
            ('全盔，改成帽冠 + 前面具', 'mecha', 'cap_mask'),
            ('半脸面具，改成全包覆', 'creature', 'full_shell'),
            ('装甲机器人', 'character', 'full_shell'),
            ('月夜狐狸面具', 'creature', 'cap_mask'),
            ('月光守望者', 'character', 'cap_mask'), ('月光守望者', 'mecha', 'full_shell'),
            ('月光守望者', 'creature', 'full_shell'),
        ]
        for theme,preset,expected in cases:
            with self.subTest(theme=theme,preset=preset):
                self.assertEqual(make_spec(theme,preset)['resolved_structure'],expected)
        with self.assertRaises(ValueError):
            make_spec('星航员','character','unknown')

    def test_sanitizes_before_creating_jobs(self):
        for value, preset in [('', 'mecha'), ('a'*(MAX_THEME+1),'character'), ('月兔','unknown'),
                              ({} ,'mecha'), ('\u0000','mecha'), ('\ud800','creature'), ('月兔',[])]:
            with self.subTest(preset=preset), self.assertRaises(ValueError):
                make_spec(value,preset)


class TextGenerationTests(unittest.TestCase):
    def test_historic_prompt_is_sent_verbatim_without_reinterpreting_its_theme(self):
        historic = {'theme':'圣诞老人','preset':'character','prompt':'旧设计：封闭的圣诞老人盔。','style':'cartoon'}
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ,{'AHOLO_API_KEY':'test-only-secret'}), \
                patch.object(lux,'_gateway',side_effect=[24, {'taskId':24,'status':6}]) as gateway:
            with self.assertRaises(lux.LuxError):
                lux.generate_text(historic, temp, lambda *_: None)
            sent = gateway.call_args_list[0].args[3]
            self.assertEqual(sent['prompt'],historic['prompt'])
            self.assertNotIn('structure',sent)

    def test_wire_contract_and_download_resume_without_image_upload(self):
        scene = json.dumps({'asset':{'version':'2.0'},'meshes':[{'primitives':[]}]}).encode()
        scene += b' ' * (-len(scene) % 4)
        glb = struct.pack('<4sIII4s',b'glTF',2,20+len(scene),len(scene),b'JSON') + scene
        spec = make_spec('圣诞老人')
        creates, polls, downloads, accepted = [], [], [], []
        def reply(request, **_kwargs):
            url = request.full_url
            if url == 'https://api.aholo.ai/global/lux3d/v1/generate/text-to-3d/task/create':
                body = json.loads(request.data)
                creates.append(body)
                data = {'c':'0','d':42}
            elif url == 'https://api.aholo.ai/global/lux3d/v1/generate/task/get?taskid=42':
                polls.append(url)
                data = {'c':'0','d':{'taskId':42,'status':3,'outputs':[
                    {'content':'https://cdn.example/result.zip'}, {'content':'https://cdn.example/result.glb'}]}}
            elif url == 'https://cdn.example/result.glb':
                self.assertFalse(request.has_header('Authorization'))
                downloads.append(url)
                if len(downloads) == 1:
                    raise ConnectionError('interrupted')
                return io.BytesIO(glb)
            else:
                self.fail('Unexpected service endpoint')
            return io.BytesIO(json.dumps(data).encode())

        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ,{'AHOLO_API_KEY':'test-only-secret','AHOLO_REGION':'com'}), \
             patch.object(lux,'build_opener') as opener:
            folder = Path(temp)
            def report(_message, task):
                if task:
                    journal = lux.read_journal(folder/'lux-task.json')
                    self.assertEqual(journal['taskId'], task)
                    accepted.append(task)
            opener.return_value.open.side_effect = reply
            with self.assertRaises(lux.NetworkError):
                lux.generate_text(spec,folder,report)
            file = lux.generate_text(spec,folder,report,resume_id='42')
            self.assertEqual(file.read_bytes(),glb)
            self.assertEqual(file,lux.generate_text(spec,folder,report))
            journal = lux.read_journal(folder/'lux-task.json')
            self.assertEqual((journal['state'],journal['operation']), ('complete','text-to-3d'))
            self.assertEqual(set(accepted), {'42'})
            self.assertEqual(len(polls),2)
            self.assertEqual(creates,[{'prompt':spec['prompt'],'style':'cartoon','version':'G1',
                                       'faceCount':100000,'outputFormat':['glb'],'aiPredictSize':False}])
            with self.assertRaises(lux.LuxError):
                lux.generate([],folder,report)

    def test_ambiguous_post_does_not_automatically_recreate(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ,{'AHOLO_API_KEY':'test-only-secret'}), \
             patch.object(lux,'_gateway',side_effect=lux.NetworkError('disconnected')) as gateway:
            for _ in range(2):
                with self.assertRaises(lux.LuxError):
                    lux.generate_text(make_spec('云豹','creature'), temp, lambda *_:None)
            self.assertEqual(gateway.call_count,1)
