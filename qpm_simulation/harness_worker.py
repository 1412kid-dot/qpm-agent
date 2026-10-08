"""One explicit operation per worker. NDJSON on stdout; library logs on stderr."""
import sys, json, threading, time, contextlib, ast, runpy, unittest
from pathlib import Path

PROTOCOL = 'qpm/1'

def emit(kind, **data):
    from .reporting import clean
    sys.__stdout__.write(json.dumps(clean(dict(protocol=PROTOCOL, kind=kind, **data)), ensure_ascii=False) + '\n')
    sys.__stdout__.flush()

def summary(result, output):
    root=Path(output)
    s=result.get('search',{})
    rows=result.get('curves',[])
    return {'result_file':str(root/'result.json'),'report_file':str(root/'report.md'),
        'figures':result.get('figures',[]),'raw_data_dir':result.get('raw_data_dir'),
        'validation':result.get('validation'),'cost':result.get('cost'),'failures':result.get('failures',[]),
        'best_geometry':result.get('best_geometry'),'search':{k:s.get(k) for k in ['best','evaluated_count','stop_reason','target_verified_within_tolerance','uncompleted']},
        'resume_available':bool(result.get('failures')),'curve_preview':rows[:3],'curve_point_count':len(rows),'spectral_metric':result.get('spectral',{}).get('metric')}

def main():
    request=json.loads(sys.stdin.readline()); cancelled=threading.Event()
    # Windows pipe reads in another thread can block process startup/teardown.
    # Cancellation is signalled by an owned file and managed process termination.
    backend=None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from . import describe_capabilities, validate_task, plan_task, run_task, postprocess
            from .environment import doctor
            from .backends import create_backend, BudgetExceeded
            action=request['action']; emit('progress',stage='validate',message='检查输入与环境')
            if action=='capabilities': value=describe_capabilities()
            elif action=='conventions':
                from .conventions import describe_conventions
                spec=request.get('task') or {}
                value=describe_conventions(spec.get('topic','overview'),spec.get('template'))
            elif action=='doctor': value=doctor()
            elif action=='plan':
                value=plan_task(request['task'])
                if request.get('workspace'):
                    from .planning import estimate_task
                    value['sampling']=estimate_task(request['task'],request['workspace'])
            elif action=='estimate':
                from .planning import estimate_task
                value=estimate_task(request['task'],request.get('workspace'))
            elif action=='plot_preset':
                from .plot_presets import render
                value=render(request['task']['run_dir'],request['task']['preset'],request['output'])
            elif action=='postprocess_export':
                from .reporting import postprocess_export
                value=postprocess_export(request['task']['run_dir'],request['output'])
            elif action=='postprocess': value=summary(postprocess(request['output']),request['output'])
            elif action in ('extension_check','extension_test','extension_adapt','extension_postprocess'):
                spec=request['task']; root=Path(spec['directory'])
                for filename in ('extension.py','test_extension.py'):
                    ast.parse((root/filename).read_text(encoding='utf-8'),filename=filename)
                value={'syntax_valid':True,'executed':False}
                if action!='extension_check':
                    sys.path.insert(0,str(root))
                    if action=='extension_adapt':
                        module=runpy.run_path(str(root/'extension.py'))
                        value=validate_task(module['adapt_task'](spec['task'])).to_dict()
                    elif action=='extension_postprocess':
                        from .extensions import result_context, export_analysis
                        source=Path(spec['result_file'])
                        source_bytes=source.read_bytes()
                        context=result_context(json.loads(source_bytes),source)
                        module=runpy.run_path(str(root/'extension.py'))
                        from copy import deepcopy
                        result=module['analyze_result'](deepcopy(context))
                        if source.read_bytes()!=source_bytes: raise ValueError('extension changed source result; prohibited')
                        value=export_analysis(result,request['output'],context,spec['extension'])
                    else:
                        module=runpy.run_path(str(root/'test_extension.py'))
                        count=0
                        for key,func in module.items():
                            if key.startswith('test_') and callable(func): func(); count+=1
                        suite=unittest.TestSuite()
                        for cls in module.values():
                            if isinstance(cls,type) and issubclass(cls,unittest.TestCase): suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
                        if suite.countTestCases():
                            tested=unittest.TextTestRunner(stream=sys.stderr).run(suite)
                            if not tested.wasSuccessful(): raise ValueError('extension tests failed')
                            count+=tested.testsRun
                        if count==0: raise ValueError('extension has no executable tests')
                        value={'syntax_valid':True,'executed':True,'tests_passed':count}
            elif action=='run':
                task=validate_task(request['task']); output=Path(request['output']); output.mkdir(parents=True,exist_ok=True)
                if cancelled.is_set(): raise BudgetExceeded('cancelled before solver startup')
                emit('progress',stage='execute',message=f'执行 {task.task_kind} / {task.backend}')
                backend=create_backend(task,output)
                check=backend.check_budget; check_time=backend.check_time; last=[0.0]
                def checkpoint():
                    if (output/'.cancel').exists(): cancelled.set()
                    if cancelled.is_set(): raise BudgetExceeded('cancelled by owning session')
                    if time.monotonic()-last[0]>.5:
                        emit('progress',stage='solve',cost=backend.summary()); last[0]=time.monotonic()
                def budget(): checkpoint(); check()
                def wall(): checkpoint(); check_time()
                backend.check_budget=budget; backend.check_time=wall
                result=run_task(task,output,backend=backend)
                value=summary(result,output)
                value['completion']='cancelled' if cancelled.is_set() else 'incomplete' if result.get('failures') else 'completed'
            else: raise ValueError('unsupported worker action')
        emit('result',value=value)
    except BaseException as exc:
        emit('error',message=f'{type(exc).__name__}: {exc}',cancelled=cancelled.is_set())
        return 1
    finally:
        if backend is not None:
            with contextlib.redirect_stdout(sys.stderr): backend.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
