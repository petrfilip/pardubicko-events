export async function loadEventData(){
  const manifestResponse=await fetch('data/manifest.json',{cache:'no-store'});
  if(!manifestResponse.ok)throw new Error(`Manifest: HTTP ${manifestResponse.status}`);
  const manifest=await manifestResponse.json();
  const weeks=await Promise.all(manifest.weeks.map(async week=>{
    const response=await fetch(week.file,{cache:'no-store'});
    if(!response.ok)throw new Error(`${week.file}: HTTP ${response.status}`);
    return response.json();
  }));
  return {manifest,events:weeks.flatMap(week=>week.events||[])};
}
