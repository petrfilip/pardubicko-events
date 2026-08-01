const dateFmt=new Intl.DateTimeFormat('cs-CZ',{weekday:'short',day:'numeric',month:'numeric',year:'numeric'});
const shortDateFmt=new Intl.DateTimeFormat('cs-CZ',{day:'numeric',month:'numeric',year:'numeric'});
const dayFmt=new Intl.DateTimeFormat('cs-CZ',{weekday:'short',day:'numeric',month:'numeric'});
const timeFmt=new Intl.DateTimeFormat('cs-CZ',{hour:'2-digit',minute:'2-digit'});

export function eventStart(event){return new Date(event.start_at)}
export function eventEnd(event){return new Date(event.end_at||event.start_at)}
export function sameDay(a,b){return a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth()&&a.getDate()===b.getDate()}
export function isFuture(event){return eventEnd(event)>=new Date()}
export function formatTime(date){return timeFmt.format(date)}
export function formatDay(date){return dayFmt.format(date)}
export function formatEventWhen(event){
  const start=eventStart(event);const end=event.end_at?eventEnd(event):null;
  if(event.all_day){return end&&!sameDay(start,end)?`${dateFmt.format(start)}\n– ${dateFmt.format(end)}`:`${dateFmt.format(start)}\ncelý den`}
  if(end&&!sameDay(start,end))return `${dateFmt.format(start)} ${timeFmt.format(start)}\n– ${shortDateFmt.format(end)} ${timeFmt.format(end)}`;
  if(end)return `${dateFmt.format(start)}\n${timeFmt.format(start)}–${timeFmt.format(end)}`;
  return `${dateFmt.format(start)}\nod ${timeFmt.format(start)}`;
}
export function sourceLabel(type){return ({official:'Oficiální zdroj',facebook:'Facebook',ticketing:'Prodej vstupenek',regional:'Regionální kalendář'})[type]||'Zdroj'}
