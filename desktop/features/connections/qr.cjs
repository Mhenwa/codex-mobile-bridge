'use strict';
const QRCode=require('qrcode');
// Local rendering only. Do not send the bearer URL to a QR image service.
async function pairingImage(grant){
  const image=await QRCode.toDataURL(grant.url,{type:'image/png',errorCorrectionLevel:'M',margin:4,width:320});
  // The one-time URL is already needed locally to draw the image. Keep the
  // exact same URL in this private desktop IPC response so it can be copied.
  // Never send it to an external QR service or persist it in settings.
  return {...grant,image};
}
module.exports={pairingImage};
