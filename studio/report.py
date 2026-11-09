"""Printable, self-contained manufacturing record for the selected project."""
from html import escape


def project_report(item):
    def value(v):
        return escape(str(v))

    report = item.get('report', {})
    checks = report.get('checks', {})
    structure = checks.get('structure') or item['params'].get('structure', 'full_shell')
    structure_name = '帽冠＋前面具 · 后脑开放' if structure == 'cap_mask' else '全包头盔'
    trial_name = '开放试戴带' if report.get('ring_kind') == 'open_band' else '试戴环'
    quality = report.get('source_quality') or item.get('source_report', {}).get('quality') or {}
    quality_note = '<p>' + value(quality['message']) + '</p>' if quality.get('message') else ''
    rows = ''.join('<tr><td>{}</td><td>{}</td><td>{:.1f} g</td><td>{:.1f} h</td></tr>'.format(
        value(plate['name']), int(plate['layers']), plate['grams'], plate['seconds'] / 3600)
        for plate in item['assets'].get('plates', []))
    ring = report.get('ring_slice')
    ring_text = (f"{ring['layers']} 层，{ring['grams']:.1f} g，约 {ring['seconds']/60:.0f} 分钟"
                 if ring else '暂无已验证的试戴件')
    notes = ''.join('<li>' + value(s) + '</li>' for s in report.get('warnings', []) + report.get('blocking', []))
    source = ('Aholo Lux3D · ' + str(item.get('remote_id') or '尚未取得任务号')
              if item['source_type'] in ('images', 'text') else '本地导入模型')
    if item['source_type'] == 'text':
        source += ' · 文字生成 · ' + item['prompt_spec']['theme']
    parts = report.get('seams', [])
    assembly = ''.join(f"<li>沿 {value(s['axis'])} 轴切分 {value(s['piece'])}，切平面 {value(s['plane_mm'])} mm，"
                       f"定位销 {value(s['pegs'])} 个。按编号将 A、B 两件试拼。</li>" for s in reversed(parts))
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{value(item['name'])} · 制作记录</title>
<style>body{{max-width:780px;margin:48px auto;padding:0 24px;font:16px/1.7 system-ui;color:#16282b}}
h1{{line-height:1.25}}small{{color:#5a6d70}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #cbd6d4}}button{{padding:8px 14px;float:right}}@media print{{button{{display:none}}}}</style>
<button onclick="print()">打印 / 保存 PDF</button><small>HelmetFit Studio · 制作记录</small>
<h1>{value(item['name'])}</h1><p>{value(source)}　·　适配版本 {int(item['revision'])}</p>
<h2>佩戴尺寸</h2><p>{structure_name}。输入头围 {value(item['params'].get('circumference'))} cm，佩戴余量 {value(item['params'].get('clearance'))} mm。
试戴截面距头模底部 {value(report.get('ring_z_mm','—'))} mm。{trial_name}切片估算为 {value(ring_text)}。</p>
<h2>几何检查</h2>{quality_note}<p>非流形边 {value(checks.get('nonmanifold_edges','—'))}，最薄抽样壁厚 {value(checks.get('sampled_wall_min_mm','—'))} mm，
戴入干涉抽样 {value(checks.get('insertion_collision_samples','—'))} 处。{value(item['message'])}</p>
<ul>{notes}</ul><h2>P1S 切片</h2><p>0.4 mm 喷嘴 · PLA · 纹理 PEI 板。下表为切片器估算。</p>
<table><thead><tr><th>零件</th><th>层数</th><th>用料</th><th>时间</th></tr></thead><tbody>{rows}</tbody></table>
<h2>装配与试戴</h2><ul>{assembly}</ul><p>先打印{trial_name}检查松紧，再决定是否打印全套。实物装配、试戴与照片由操作者另行记录。</p></html>"""
