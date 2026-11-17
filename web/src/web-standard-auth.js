/* 인증 요청/토큰/쿠키를 다루지 않는 공통 표현 계층. 기존 노드와 이벤트를 보존한다. */
(function(){
'use strict';
var project=(document.documentElement.className.match(/ws-project-([\w-]+)/)||[])[1];
var names={bali:'메모 앱',canggu:'Kubernetes 자동 운영',pangyo:'MemoNest',samsung:'TrafficPolicy',seongnam:'OS Monitor',seoul:'클러스터 점검'};
if(!names[project])return;
var pendingPassword=project==='samsung'&&/\/settings\/?$/.test(location.pathname);
var passwordDialog=null;
function element(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;}
function heading(title,subtitle,mark){var h=element('div','ws-auth-heading');if(mark){var icon=element('div','ws-auth-mark');icon.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></svg>';h.append(icon);}h.append(element('h2','',title),element('p','',subtitle));return h;}
function hide(e){if(e)e.setAttribute('data-ws-auth-hide','');}
function original(e){if(e)e.setAttribute('data-ws-original-action','');}
function available(e){return !!e&&e.isConnected&&!e.closest('[hidden]');}
function login(card,screen,form){if(!card||card.dataset.wsAuthReady)return;card.dataset.wsAuthReady='1';card.classList.add('ws-auth-card');if(screen)screen.classList.add('ws-auth-screen');
 if(project==='bali'){hide(card.firstElementChild);var buttons=card.querySelectorAll('button');buttons.forEach(function(b){if(/로그인/.test(b.textContent))b.classList.add('ws-auth-submit');});hide(card.lastElementChild&&card.lastElementChild.tagName==='P'?card.lastElementChild:null);}
 else {Array.from(card.children).forEach(function(c){if(c.matches('h1,h2,h3,.sub,.muted'))hide(c);});}
 card.prepend(heading('로그인',names[project]+' 계정으로 로그인하세요.',true));
 var fields=card.querySelectorAll('input:not([type=hidden]):not([type=checkbox])');fields.forEach(function(input,index){if(!input.id)input.id='ws-login-'+project+'-'+index;if(!input.closest('label')&&!card.querySelector('label[for="'+input.id+'"]')){var label=element('label','ws-auth-field',input.type==='password'?'비밀번호':'아이디');label.htmlFor=input.id;input.before(label);}if(input.type==='password')input.autocomplete='current-password';else if(!input.autocomplete)input.autocomplete='username';});
 if(form)form.setAttribute('aria-label','로그인');
}
function stylePassword(card,form,cancel){card.classList.add('ws-password-card');var old=card.querySelector(':scope > h2,:scope > h3');hide(old);card.prepend(heading('비밀번호 변경','현재 비밀번호를 확인한 뒤 새 비밀번호를 저장합니다.',false));
 var action=form?form.querySelector('button[type=submit]'):card.querySelector('.edit-actions .primary');if(!action&&form)action=form.querySelector('button.btn');
 var actions=element('div','ws-password-actions');if(form){if(cancel)actions.append(cancel);if(action)actions.append(action);form.append(actions);}else{var row=card.querySelector('.edit-actions');if(row)row.classList.add('ws-password-actions');}
 if(action)action.textContent='변경 사항 저장';
 var inputs=Array.from((form||card).querySelectorAll('input[type=password]'));
 if(inputs.length===2){var label=element('label','ws-auth-field','새 비밀번호 확인');var confirm=element('input');confirm.type='password';confirm.autocomplete='new-password';confirm.required=true;confirm.dataset.wsConfirm='1';label.append(confirm);if(form)form.insertBefore(label,actions);else{var grid=card.querySelector('.edit-grid');grid.append(label);}var status=element('p','ws-auth-feedback');status.setAttribute('role','alert');label.after(status);
 var validate=function(event){if(confirm.value!==inputs[1].value||!confirm.value){event.preventDefault();event.stopImmediatePropagation();status.textContent='새 비밀번호가 일치하지 않습니다.';confirm.focus();}else status.textContent='';};if(action)action.addEventListener('click',validate,true);if(form)form.addEventListener('submit',validate,true);}
 card.setAttribute('aria-label','비밀번호 변경');
}
function modalize(form){if(!form||form.dataset.wsPasswordReady)return;form.dataset.wsPasswordReady='1';var parent=form.parentElement;parent.classList.add('ws-security-panel');var dialog=element('dialog','ws-password-card');var open=element('button','ws-security-open','비밀번호 변경');open.type='button';var cancel=element('button','','취소');cancel.type='button';cancel.addEventListener('click',function(){dialog.close();});form.before(open,dialog);dialog.append(form);stylePassword(dialog,form,cancel);open.addEventListener('click',function(){dialog.showModal();});passwordDialog=dialog;if(pendingPassword){pendingPassword=false;dialog.showModal();}}
function menu(host,logout,password,account){if(!host||host.querySelector(':scope > .ws-account-menu'))return;var detail=element('details','ws-account-menu');var summary=element('summary','','계정');var panel=element('div','ws-account-dropdown');detail.append(summary,panel);
 function action(text,callback,cls){var b=element('button',cls,text);b.type='button';b.addEventListener('click',function(){detail.open=false;callback();});panel.append(b);}
 if(account){action('계정 관리',function(){account.click();});original(account);}
 if(password){action('비밀번호 변경',function(){if(typeof password==='function')password();else password.click();});if(typeof password!=='function')original(password);}
 if(logout){action('로그아웃',function(){logout.click();},'ws-signout');original(logout);}
 host.append(detail);
}
function requestPassword(){pendingPassword=true;var settings=document.querySelector('[data-tab=settings],[data-hash="#settings"]');if(settings)settings.click();setTimeout(schedule,0);}
function enhance(){
 if(project==='bali'){var input=document.getElementById('username');if(input){var card=input.closest('div[style]');while(card&&(!card.querySelector('button')||!card.querySelector('input[type=password]')))card=card.parentElement;if(card){var outer=card.parentElement;if(outer&&outer.querySelectorAll('input').length===2){login(outer,outer.parentElement,null);}}}var logout=Array.from(document.querySelectorAll('header button')).find(function(b){return b.textContent==='로그아웃'&&!b.closest('.ws-account-menu');});if(logout)menu(logout.parentElement,logout,null,null);}
 if(project==='canggu'){var form=document.querySelector('form.login');if(form)login(form,form.parentElement,form);var pw=Array.from(document.querySelectorAll('header > button')).find(function(b){return b.textContent==='비밀번호 변경';});var lo=Array.from(document.querySelectorAll('header > button')).find(function(b){return b.textContent==='로그아웃';});if(lo)menu(lo.parentElement,lo,pw,null);var modal=document.querySelector('.modal-backdrop .modal');if(modal&&modal.querySelector('input[type=password]')&&!modal.dataset.wsPasswordReady){modal.dataset.wsPasswordReady='1';stylePassword(modal,null,null);modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');}}
 if(project==='seoul'||project==='seongnam'){var overlay=document.getElementById('login-overlay');var form=document.getElementById('login-form');if(form)login(project==='seoul'?form:form.parentElement,overlay,form);var lo=document.getElementById('logout-btn');if(available(lo))menu(lo.parentElement,lo,requestPassword,null);modalize(document.getElementById(project==='seoul'?'password-form':'pw-form'));}
 if(project==='samsung'){var form=document.querySelector('form[action$="/login"]');if(form){login(form.parentElement,form.closest('main'),form);document.body.classList.add('ws-auth-page');}var settings=document.querySelector('form[action$="/settings"]');if(settings)modalize(settings);var logout=document.querySelector('form[action$="/logout"] button');var link=document.querySelector('.userbox a[href$="/settings"]');if(logout)menu(logout.closest('.userbox'),logout,link,null);}
 if(project==='pangyo'){var btn=document.getElementById('account-btn');if(btn)btn.classList.add('ws-account-trigger');var form=document.getElementById('account-login-form');if(form){var dialog=form.closest('dialog');if(dialog){login(dialog,null,form);dialog.classList.add('ws-auth-card');}}else{var dialog=document.getElementById('account-dialog');if(dialog&&dialog.dataset.wsAuthReady){dialog.classList.remove('ws-auth-card');delete dialog.dataset.wsAuthReady;var h=dialog.querySelector(':scope > .ws-auth-heading');if(h)h.remove();}}}
 if(pendingPassword&&passwordDialog&&passwordDialog.isConnected&&passwordDialog.parentElement.getClientRects().length){pendingPassword=false;if(!passwordDialog.open)passwordDialog.showModal();}
}
var scheduled=false;function schedule(){if(scheduled)return;scheduled=true;requestAnimationFrame(function(){scheduled=false;enhance();});}
document.addEventListener('keydown',function(e){var modal=document.querySelector('.modal-backdrop .ws-password-card');if(modal&&e.key==='Tab'){var nodes=Array.from(modal.querySelectorAll('input,button')).filter(function(n){return !n.disabled&&n.getClientRects().length;});var first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}if(modal&&e.key==='Enter'&&e.target.tagName==='INPUT'){e.preventDefault();var submit=modal.querySelector('button.primary');if(submit)submit.click();}if(e.key==='Escape'){document.querySelectorAll('.ws-account-menu[open]').forEach(function(d){d.open=false;});var card=document.querySelector('.modal-backdrop .ws-password-card');if(card){var cancel=card.querySelector('.edit-actions button');if(cancel)cancel.click();}}});
document.addEventListener('click',function(e){document.querySelectorAll('.ws-account-menu[open]').forEach(function(d){if(!d.contains(e.target))d.open=false;});});
function start(){enhance();new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden']});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
}());
