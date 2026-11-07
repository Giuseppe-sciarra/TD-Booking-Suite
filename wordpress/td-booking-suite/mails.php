<?php
/* ---------- 2.21.0 Il sito gestisce prenotazioni ed email: turned on in the program (Sito WordPress · modalità PC, with the
   archive), the site confirms the bookings made on it at once and sends every email: the confirmation, the change, the
   cancellation, the reminders and the notices to the business, also with the PC off for days. The texts are set here
   (Impostazioni › TD Booking Suite › Email ai clienti). The PC sends no email: it gets the bookings, and the changes the
   clients make with the links of the emails (cancelled, Confermo, Non vengo), at its next sync.
   What the site knows of each booking is in tdbs_state: from the archive the PC pushes (the desk's bookings too) and
   from the bookings made here; an email goes when a booking changes after the program turned this on (since), never for
   the history. The links of the emails are signed here with the shared secret. */
defined('ABSPATH') || exit;

const TDBS_MAIL_DB='2';
const TDBS_MAIL_KINDS=array(
    'confirmed'=>array('Conferma','Appena la prenotazione è fatta, online o al banco'),
    'requested'=>array('Richiesta ricevuta','Per i servizi che confermate voi: la richiesta arrivata'),
    'moved'=>array('Modifica','Quando la prenotazione cambia giorno o orario'),
    'cancelled'=>array('Annullamento','Quando la prenotazione viene annullata, da voi o dal cliente'),
    'reminder'=>array('Promemoria','Prima dell’appuntamento, con i tasti Confermo e Annulla'),
    'reminder2'=>array('Secondo promemoria','Poche ore prima, facoltativo'),
    'recheck'=>array('Da ricontrollare','Una prenotazione dal sito su un orario che nel frattempo è stato preso al banco'),
    'refused'=>array('Non registrata','Una prenotazione dal sito che il programma non ha potuto tenere'),
    // 2.22.0: these four the site decides by itself (tdbs_decide): from the archive, the snapshot and the waiting list the PC sends
    'arrival'=>array('Prima dell’arrivo','Soggiorni ed eventi: le istruzioni scritte nel servizio nel programma (check-in, punto d’incontro), ore prima dell’inizio'),
    'review'=>array('Dopo la visita','Il grazie con il link per la recensione, ore dopo la fine, a chi è iscritto alle comunicazioni, al massimo una volta a settimana'),
    'recall'=>array('Richiamo','Quando è ora di tornare per un servizio con il richiamo, a chi è iscritto alle comunicazioni e non ha già un appuntamento'),
    'waitlist'=>array('Si è liberato un posto','A chi è in lista d’attesa, quando si libera il posto che aspetta, con il link per prenotarlo'),
);
function tdbs_mail_defaults() {
    return array(
        'notify'=>'',
        'confirmed'=>array('subject'=>'Prenotazione confermata · {attivita}','body'=>"Ciao {nome},\nla prenotazione è confermata.\n{servizio}\n{quando}\nPer spostarla o annullarla usa il tasto qui sotto."),
        'requested'=>array('subject'=>'Richiesta ricevuta · {attivita}','body'=>"Ciao {nome},\nabbiamo ricevuto la tua richiesta: {servizio}, {quando}.\nTi scriviamo appena la confermiamo."),
        'moved'=>array('subject'=>'Prenotazione aggiornata · {attivita}','body'=>"Ciao {nome},\nla prenotazione è cambiata: {servizio}, {quando}."),
        'cancelled'=>array('subject'=>'Prenotazione annullata · {attivita}','body'=>"Ciao {nome},\nla prenotazione di {servizio}, {quando}, è annullata.\nPer prenotare di nuovo contattaci."),
        'reminder'=>array('on'=>true,'hours'=>24,'subject'=>'Promemoria · {attivita}','body'=>"Ciao {nome},\nti ricordiamo la prenotazione: {servizio}, {quando}.\nSe non puoi venire, annulla con il tasto qui sotto."),
        'reminder2'=>array('on'=>false,'hours'=>2,'subject'=>'A tra poco · {attivita}','body'=>"Ciao {nome},\nti aspettiamo tra poco: {servizio}, {quando}."),
        'recheck'=>array('subject'=>'La tua prenotazione va ricontrollata · {attivita}','body'=>"Ciao {nome},\nl’orario che hai scelto, {quando}, nel frattempo è stato occupato: ti ricontattiamo per confermarlo o spostarlo."),
        'refused'=>array('subject'=>'Prenotazione non registrata · {attivita}','body'=>"Ciao {nome},\nla prenotazione di {servizio}, {quando}, non è stata registrata: {motivo}.\nContattaci per prenotare."),
        'arrival'=>array('on'=>false,'hours'=>48,'subject'=>'Prima del tuo arrivo · {attivita}','body'=>"Ciao {nome},\nti aspettiamo: {servizio}, {quando}.\n{istruzioni}\nPer gestire la prenotazione usa il tasto qui sotto."),
        'review'=>array('on'=>true,'hours'=>3,'url'=>'','subject'=>'Com’è andata? · {attivita}','body'=>"Ciao {nome},\ngrazie di aver scelto {attivita}. Se ti va, lasciaci una recensione: per noi conta molto."),
        'recall'=>array('on'=>false,'subject'=>'È ora di tornare · {attivita}','body'=>"Ciao {nome},\nè il momento giusto per {servizio}. Prenota quando vuoi con il tasto qui sotto."),
        'waitlist'=>array('on'=>true,'subject'=>'Si è liberato un posto · {attivita}','body'=>"Ciao {nome},\nsi è liberato un posto che aspettavi: {servizio}, {quando}.\n{nota}\nPrenota con il tasto qui sotto."),
    );
}
function tdbs_mail_conf() {
    $d=tdbs_mail_defaults();$s=get_option('tdbs_mail',array());$s=is_array($s)?$s:array();
    foreach ($d as $k=>$v) if (is_array($v)) $d[$k]=array_merge($v,array_intersect_key(is_array($s[$k]??null)?$s[$k]:array(),$v));
    $d['notify']=(string)($s['notify']??'');
    return $d;
}
/** On only when the program says so, with the shared secret: the program sends no email then. */
function tdbs_master() {$m=(array)(tdbs_pc_snapshot()['master']??array());return tdbs_pc_on() && !empty($m['on']);}
function tdbs_master_since() {$m=(array)(tdbs_pc_snapshot()['master']??array());return (int)($m['since']??0);}

function tdbs_mail_tables() {
    global $wpdb;$state=$wpdb->prefix.'tdbs_state';$changes=$wpdb->prefix.'tdbs_changes';
    if (get_option('tdbs_mail_db')!==TDBS_MAIL_DB) {
        require_once ABSPATH.'wp-admin/includes/upgrade.php';$cs=$wpdb->get_charset_collate();
        dbDelta("CREATE TABLE $state (\n ref varchar(40) NOT NULL,\n alias varchar(40) NOT NULL DEFAULT '',\n status varchar(12) NOT NULL DEFAULT '',\n start bigint(20) NOT NULL DEFAULT 0,\n seen bigint(20) NOT NULL DEFAULT 0,\n cs varchar(80) NOT NULL DEFAULT '',\n flags text NOT NULL,\n data longtext NOT NULL,\n PRIMARY KEY  (ref),\n KEY cs (cs),\n KEY alias (alias),\n KEY start (start)\n) $cs;");
        dbDelta("CREATE TABLE $changes (\n id bigint(20) unsigned NOT NULL AUTO_INCREMENT,\n ref varchar(40) NOT NULL,\n action varchar(12) NOT NULL,\n at varchar(40) NOT NULL,\n PRIMARY KEY  (id)\n) $cs;");
        update_option('tdbs_mail_db',TDBS_MAIL_DB);
    }
    return array($state,$changes);
}
/** A booking as the site knows it: its row with the client's data opened, or null. By its id, or by the id it had here (wp-…). */
function tdbs_state_get($ref) {
    global $wpdb;list($state)=tdbs_mail_tables();
    $row=$wpdb->get_row($wpdb->prepare("SELECT * FROM $state WHERE ref=%s OR alias=%s LIMIT 1",$ref,$ref),ARRAY_A);
    if (!$row) return null;
    $row['info']=tdbs_pc_open($row['data']);$row['flags']=json_decode((string)$row['flags'],true);
    if (!is_array($row['info'])) return null;
    if (!is_array($row['flags'])) $row['flags']=array();
    return $row;
}
function tdbs_state_put($ref,$status,$start,$info,$flags,$seen=null,$alias=null) {
    global $wpdb;list($state)=tdbs_mail_tables();
    $row=array('ref'=>$ref,'status'=>$status,'start'=>(int)$start,'flags'=>wp_json_encode((object)$flags),'data'=>tdbs_pc_seal($info),'cs'=>(string)($info['contact_id']??'').'.'.(string)($info['service_id']??''));
    if ($seen!==null) $row['seen']=(int)$seen;
    if ($alias!==null) $row['alias']=$alias;
    if ($wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM $state WHERE ref=%s",$ref))) $wpdb->update($state,$row,array('ref'=>$ref));
    else $wpdb->insert($state,$row+array('seen'=>(int)($seen??time()),'alias'=>(string)($alias??'')));
}
function tdbs_change($ref,$action) {
    global $wpdb;list(,$changes)=tdbs_mail_tables();
    $wpdb->insert($changes,array('ref'=>$ref,'action'=>$action,'at'=>gmdate('c')));
}

/* ---------- the texts */
function tdbs_tz() {try {return new DateTimeZone((string)(tdbs_pc_snapshot()['timezone']??'Europe/Rome'));} catch (Exception $e) {return wp_timezone();}}
const TDBS_DAYS=array('lunedì','martedì','mercoledì','giovedì','venerdì','sabato','domenica');
/** {nome}, {servizio}, {quando}…, as the program writes them. */
function tdbs_values($info,$ref,$extra=array()) {
    $d=new DateTime('@'.(int)$info['start']);$d->setTimezone(tdbs_tz());
    return array_merge(array('nome'=>(string)($info['first_name']??''),'servizio'=>(string)($info['service']??''),
        'quando'=>TDBS_DAYS[(int)$d->format('N')-1].' '.$d->format('d/m/Y').' alle '.$d->format('H:i'),'data'=>$d->format('d/m/Y'),'ora'=>$d->format('H:i'),
        'sede'=>(string)($info['location']??''),'operatore'=>(string)($info['operator']??''),'attivita'=>(string)(tdbs_pc_snapshot()['name']??''),
        'link'=>tdbs_link($ref,(int)$info['start']),'istruzioni'=>'','motivo'=>'','nota'=>''),$extra);
}
function tdbs_fill($text,$v) {return preg_replace_callback('/\{(\w+)\}/',function($m)use($v){return array_key_exists($m[1],$v)?(string)$v[$m[1]]:$m[0];},(string)$text);}
/** The email of this moment to the client of the booking, if he left an email and did not say no to these emails. */
function tdbs_tell($kind,$ref,$info,$extra=array()) {
    $email=(string)($info['email']??'');
    if (!is_email($email) || ($info['notices']??true)===false) return false;
    $c=tdbs_mail_conf()[$kind]??null;
    if (!is_array($c) || (array_key_exists('on',$c) && empty($c['on']))) return false;
    $v=tdbs_values($info,$ref,$extra);$buttons=array();
    if ($kind==='reminder' || $kind==='reminder2') $buttons=array(array('Confermo, ci sarò',tdbs_link($ref,(int)$info['start'],'confirm')),array('Annulla',$v['link']));
    elseif (in_array($kind,array('confirmed','requested','moved','arrival'),true)) $buttons=array(array('Gestisci la prenotazione',$v['link']));
    elseif ($kind==='review') {$v['link']=(string)$c['url'];$buttons=array(array('Lascia una recensione',$v['link']));}
    elseif ($kind==='recall' || $kind==='waitlist') {$v['link']=tdbs_book_link();$buttons=array(array('Prenota',$v['link']));}
    return tdbs_send($email,tdbs_fill($c['subject'],$v),tdbs_fill($c['body'],$v),$buttons);
}
/** To the business (Avvisi a voi): a booking made, cancelled or declined by a client on the site. */
function tdbs_notify($what,$info) {
    $to=(string)tdbs_mail_conf()['notify'];
    if (!is_email($to)) return false;
    $v=tdbs_values($info,'');$who=trim($v['nome'].' '.($info['last_name']??''));
    $text=$what."\n".$v['servizio']."\n".$v['quando'].($v['operatore']!==''?"\nCon: ".$v['operatore']:'')."\nCliente: ".$who.(!empty($info['phone'])?"\nTelefono: ".$info['phone']:'').(!empty($info['email'])?"\nEmail: ".$info['email']:'');
    return tdbs_send($to,$what.' · '.$v['servizio'],$text);
}
/** The email, with the HTML part in the business's colour (as the program's) and the text part; through wp_mail, so with
    the SMTP the site uses for its emails. */
function tdbs_send($to,$subject,$text,$buttons=array()) {
    $snap=tdbs_pc_snapshot();$name=(string)($snap['name']??get_bloginfo('name'));
    $color=preg_match('/^#[0-9a-fA-F]{6}$/',(string)($snap['color']??''))?$snap['color']:'#6d4aff';
    $plain=$text.implode('',array_map(function($b){return "\n".$b[0].': '.$b[1];},$buttons));
    $links='';foreach ($buttons as $i=>$b) $links.='<a href="'.esc_url($b[1]).'" style="display:inline-block;margin:0 8px 8px 0;padding:11px 22px;border-radius:8px;border:1px solid '.$color.';'.($i===0?'background:'.$color.';color:#ffffff':'background:#ffffff;color:'.$color).';font-weight:bold;text-decoration:none">'.esc_html($b[0]).'</a>';
    $html='<!DOCTYPE html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'.esc_html($name).'</title></head><body style="margin:0;padding:24px 12px;background:#f5f4f7;font-family:Arial,Helvetica,sans-serif;color:#222"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;border-top:4px solid '.$color.'">'
        .'<tr><td style="padding:24px 28px 4px"><strong style="font-size:18px;color:'.$color.'">'.esc_html($name).'</strong></td></tr>'
        .'<tr><td style="padding:12px 28px 4px;font-size:15px;line-height:1.6">'.make_clickable(nl2br(esc_html($text))).'</td></tr>'
        .($links?'<tr><td style="padding:16px 28px 4px">'.$links.'</td></tr>':'').'<tr><td style="padding:20px 28px 24px;font-size:12px;color:#777777">'.esc_html($name).'</td></tr></table></td></tr></table></body></html>';
    $alt=function($m)use($plain){$m->AltBody=$plain;};
    $from=function()use($name){return $name;};
    add_action('phpmailer_init',$alt);add_filter('wp_mail_from_name',$from);
    $ok=wp_mail($to,$subject,$html,array('Content-Type: text/html; charset=UTF-8'));
    remove_action('phpmailer_init',$alt);remove_filter('wp_mail_from_name',$from);
    if (!$ok) update_option('tdbs_mail_error',array('at'=>time(),'to'=>preg_replace('/^(.).*(@.*)$/','$1…$2',$to)),false);
    else update_option('tdbs_mail_last',time(),false);
    return $ok;
}

/* ---------- the links of the emails: signed here, checked here, no PC needed */
function tdbs_b64u($raw) {return rtrim(strtr(base64_encode($raw),'+/','-_'),'=');}
function tdbs_link_sig($body) {return tdbs_b64u(substr(hash_hmac('sha256','tdbs-link.'.$body,(string)(tdbs_pc()['secret']??''),true),0,16));}
/** The booking, what the link does (m: the page, c: Confermo) and until when, in base 36: valid 30 days after the start. */
function tdbs_link($ref,$start,$what='manage') {
    if ($ref==='' || empty(tdbs_pc()['secret'])) return '';
    $body=$ref.'.'.($what==='confirm'?'c':'m').'.'.base_convert((string)(max(time(),(int)$start)+30*DAY_IN_SECONDS),10,36);
    return add_query_arg('tdbs',$body.'.'.tdbs_link_sig($body),home_url('/'));
}
function tdbs_link_open($token) {
    if (empty(tdbs_pc()['secret']) || !preg_match('/^((wp-[a-f0-9]{32}|[0-9a-f-]{36})\.([mc])\.([0-9a-z]{1,10}))\.([A-Za-z0-9_-]{22})$/',(string)$token,$m)) return null;
    if (!hash_equals(tdbs_link_sig($m[1]),$m[5]) || (int)base_convert($m[4],36,10)<=time()) return null;
    return array('r'=>$m[2],'k'=>$m[3]==='c'?'confirm':'manage');
}

/* ---------- what the PC pushes: the site's knowledge of each booking, and the emails of what changed */
/** From the archive's record of a booking (core/wpsync.pack): the data an email needs, and the client's "no" to these emails. */
function tdbs_info_of($rec) {
    global $wpdb;
    $d=(array)($rec['data']??array());$n=(array)($rec['names']??array());$s=(array)($rec['snapshot']??array());
    $notices=($s['notices']??true)!==false;
    $card=tdbs_card((string)($rec['contact_id']??''));
    if ($card && array_key_exists('notices',$card) && !$card['notices']) $notices=false;
    $service=(string)($s['service']??($n['service']??''));if (!empty($n['option'])) $service.=' · '.$n['option'];
    return array('first_name'=>(string)($d['first_name']??''),'last_name'=>(string)($d['last_name']??''),'email'=>(string)($d['email']??''),'phone'=>(string)($d['phone']??''),
        'notices'=>$notices,'service'=>$service,'service_id'=>(string)($rec['service_id']??''),'operator'=>(string)($n['resource']??''),'location'=>(string)($n['location']??''),
        'start'=>strtotime((string)($rec['start']??'')),'end'=>strtotime((string)($rec['end']??'')),
        // 2.22.0: for Dopo la visita and Richiamo: the card and its consent to the communications (art. 130: they invite)
        'contact_id'=>(string)($rec['contact_id']??''),'consent'=>$card && ($card['consent']??'')==='yes' && empty($card['suppressed']));
}
/** The client's card of the archive, opened, or null. */
function tdbs_card($id) {
    global $wpdb;
    if ($id==='') return null;
    $card=$wpdb->get_var($wpdb->prepare('SELECT data FROM '.tdbs_archive_table()." WHERE kind='contact' AND ref=%s",$id));
    $card=$card?tdbs_pc_open($card):null;
    return is_array($card)?$card:null;
}
/** One booking of the archive push: what the site knows of it updated; the email of the change when it changed after since
    (the program turned this on): new, accepted, moved, cancelled. A booking of a visit after its first service, of a series,
    imported from the old program or written in the site's agenda and still to check gets none, as in the program. */
function tdbs_state_from_archive($ref,$rec,$v) {
    global $wpdb;list($state)=tdbs_mail_tables();
    $rk=(string)($rec['request_key']??'');
    // a booking made here, now the PC's: the same row, under the PC's id
    if (preg_match('/^wp-[a-f0-9]{32}$/',$rk) && !$wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM $state WHERE ref=%s",$ref)))
        $wpdb->update($state,array('ref'=>$ref,'alias'=>$rk),array('ref'=>$rk));
    $old=tdbs_state_get($ref);$info=tdbs_info_of($rec);$status=(string)($rec['status']??'');
    if (!$info['start']) return;
    $live=$v>tdbs_master_since() && tdbs_master();
    $quiet=!empty($rec['series']) || preg_match('/-v\d+$/',$rk) || ($rec['origin']??'')==='import';
    $flags=$old?$old['flags']:array();$mail='';
    if (!$old) {
        if ($live && !$quiet) $mail=$status==='confirmed'?'confirmed':($status==='pending' && ($rec['origin']??'')!=='agenda'?'requested':'');
    } elseif (!empty($flags['cancelled'])) {
        // cancelled here by the client: the PC follows, nothing more to tell
    } elseif ($live && !$quiet) {
        if ($status==='cancelled' && $old['status']!=='cancelled') $mail='cancelled';
        elseif ($old['status']==='pending' && $status==='confirmed') $mail='confirmed';
        elseif (in_array($status,array('pending','confirmed'),true) && (int)$old['start']!==$info['start']) $mail='moved';
    }
    if ((int)($old['start']??0)!==$info['start']) unset($flags['reminded'],$flags['reminded2'],$flags['confirmed']);  // a new day: new reminders
    if (!empty($rec['snapshot']['client_confirmed'])) $flags['confirmed']=1;
    $seen=$old?null:(strtotime((string)($rec['created']??''))?:time());
    tdbs_state_put($ref,$status,$info['start'],$info,$flags,$seen);
    if ($mail) tdbs_tell($mail,$ref,$info);
}

/* ---------- the reminders, every 5 minutes (WP-Cron: with a system cron it is on time also without visits to the site) */
add_filter('cron_schedules',function($s){$s['tdbs_5']=array('interval'=>300,'display'=>'Ogni 5 minuti (TD Booking Suite)');return $s;});
add_action('init',function(){if (!wp_next_scheduled('tdbs_mail_cron')) wp_schedule_event(time()+60,'tdbs_5','tdbs_mail_cron');});
add_action('tdbs_mail_cron','tdbs_remind');
add_action('tdbs_mail_cron','tdbs_decide');
function tdbs_remind($now=null) {
    global $wpdb;
    if (!tdbs_master()) return 0;
    list($state)=tdbs_mail_tables();$now=$now??time();$conf=tdbs_mail_conf();$sent=0;
    $rows=(array)$wpdb->get_col($wpdb->prepare("SELECT ref FROM $state WHERE status='confirmed' AND start>%d AND start<=%d ORDER BY start LIMIT 500",$now,$now+73*HOUR_IN_SECONDS));
    foreach ($rows as $ref) {
        $b=tdbs_state_get($ref);
        if (!$b || !empty($b['flags']['cancelled'])) continue;
        foreach (array('reminder'=>'reminded','reminder2'=>'reminded2') as $kind=>$mark) {
            $c=$conf[$kind];
            if (empty($c['on']) || !empty($b['flags'][$mark])) continue;
            $due=(int)$b['start']-max(1,(int)$c['hours'])*HOUR_IN_SECONDS;
            if ($now<$due) continue;
            // who booked when the reminder was already due (for this afternoon) needs none
            if ((int)$b['seen']<=$due && tdbs_tell($kind,$b['ref'],$b['info'])) $sent++;
            $b['flags'][$mark]=$now;
            tdbs_state_put($b['ref'],$b['status'],$b['start'],$b['info'],$b['flags']);
        }
    }
    return $sent;
}

/* ---------- 2.22.0 the four the site decides by itself, as the program would: Prima dell'arrivo (the instructions of the
   service, hours before), Dopo la visita (hours after a visit marked Fatta, with the consent, once a week per client),
   Richiamo (the last visit of a service with a recall, its day reached, nothing booked after, once a day), Si è liberato un
   posto (the waiting list of the PC against the free times here, once per time). The instructions and the recall days
   come with the snapshot (extra), the entries with the archive (kind wait). */
function tdbs_book_link() {$p=tdbs_booking_page(false);return $p?get_permalink($p):home_url('/');}
function tdbs_decide($now=null) {
    global $wpdb;
    if (!tdbs_master()) return 0;
    list($state)=tdbs_mail_tables();$now=$now??time();$conf=tdbs_mail_conf();$sent=0;
    $snap=tdbs_pc_snapshot();$extra=(array)($snap['extra']??array());
    $c=$conf['arrival'];
    if (!empty($c['on']) && $extra) foreach ((array)$wpdb->get_col($wpdb->prepare("SELECT ref FROM $state WHERE status='confirmed' AND start>%d AND start<=%d ORDER BY start LIMIT 500",$now,$now+max(1,(int)$c['hours'])*HOUR_IN_SECONDS)) as $ref) {
        $b=tdbs_state_get($ref);if (!$b || isset($b['flags']['arrival'])) continue;
        $text=(string)($extra[(string)($b['info']['service_id']??'')]['instructions']??'');
        if ($text!=='' && tdbs_tell('arrival',$ref,$b['info'],array('istruzioni'=>$text))) $sent++;
        $b['flags']['arrival']=$now;tdbs_state_put($ref,$b['status'],$b['start'],$b['info'],$b['flags']);
    }
    $c=$conf['review'];
    if (!empty($c['on']) && (string)$c['url']!=='') {
        $after=$now-max(1,(int)$c['hours'])*HOUR_IN_SECONDS;
        foreach ((array)$wpdb->get_col($wpdb->prepare("SELECT ref FROM $state WHERE status='completed' AND start>%d AND start<=%d ORDER BY start LIMIT 500",$after-3*DAY_IN_SECONDS,$after)) as $ref) {
            $b=tdbs_state_get($ref);if (!$b || isset($b['flags']['review'])) continue;
            $end=(int)($b['info']['end']??0);
            if ($end>$after) continue;
            // not the old ones when it is switched on, never twice in 7 days to the same client
            $go=$end>$after-2*DAY_IN_SECONDS && !empty($b['info']['consent']) && !tdbs_told_lately($b['info'],'review',$now-7*DAY_IN_SECONDS);
            $b['flags']['review']=$go && tdbs_tell('review',$ref,$b['info'])?$now:0;  // 0: looked at, not sent
            $sent+=(int)(bool)$b['flags']['review'];tdbs_state_put($ref,$b['status'],$b['start'],$b['info'],$b['flags']);
        }
    }
    $c=$conf['recall'];$day=gmdate('Y-m-d',$now);
    if (!empty($c['on']) && $extra && get_option('tdbs_recall_day')!==$day) {
        update_option('tdbs_recall_day',$day,false);
        foreach ($extra as $sid=>$x) {
            $days=(int)($x['recall']??0);if ($days<1) continue;
            // shortcut: the visits whose recall came due in the last 3 days; a site without visits for longer skips them
            foreach ((array)$wpdb->get_col($wpdb->prepare("SELECT ref FROM $state WHERE status='completed' AND cs LIKE %s AND start>%d AND start<=%d LIMIT 500",'%.'.$wpdb->esc_like((string)$sid),$now-($days+3)*DAY_IN_SECONDS,$now-$days*DAY_IN_SECONDS)) as $ref) {
                $b=tdbs_state_get($ref);if (!$b || isset($b['flags']['recall']) || empty($b['info']['consent'])) continue;
                $cs=(string)($b['info']['contact_id']??'').'.'.$sid;$contact=(string)($b['info']['contact_id']??'');
                $last=(int)$wpdb->get_var($wpdb->prepare("SELECT MAX(start) FROM $state WHERE cs=%s AND status='completed'",$cs));
                $booked=$contact!=='' && $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM $state WHERE cs LIKE %s AND status IN ('pending','confirmed') AND start>%d",$wpdb->esc_like($contact).'.%',$now));
                $go=$last===(int)$b['start'] && !$booked;
                $b['flags']['recall']=$go && tdbs_tell('recall',$ref,$b['info'])?$now:0;
                $sent+=(int)(bool)$b['flags']['recall'];tdbs_state_put($ref,$b['status'],$b['start'],$b['info'],$b['flags']);
            }
        }
    }
    $c=$conf['waitlist'];
    if (!empty($c['on']) && !empty($snap['services'])) foreach ((array)$wpdb->get_results('SELECT ref,data FROM '.tdbs_archive_table()." WHERE kind='wait' AND data<>'' LIMIT 500",ARRAY_A) as $row) {
        $e=tdbs_pc_open($row['data']);
        if (!is_array($e) || ($e['status']??'')!=='waiting') continue;
        $sv=tdbs_pc_service($snap,(string)($e['service_id']??''));if (!$sv) continue;  // a service the site does not take (shortcut: only appointments with operator)
        try {$tz=new DateTimeZone((string)($snap['timezone']??'Europe/Rome'));} catch (Exception $ex) {$tz=wp_timezone();}
        $today=(new DateTime('@'.$now))->setTimezone($tz)->format('Y-m-d');
        $first=(string)($e['first']??'');$last=(string)($e['last']??$first);
        if ($last<$today) continue;
        $st=tdbs_state_get($row['ref']);$seen=$st?(array)($st['flags']['seen']??array()):array();
        $from=(string)($e['time_from']??'');$to=(string)($e['time_to']??'');$free=tdbs_pc_free($snap,$sv,'',(string)($e['resource_id']??''));
        $times=array();$when='';
        foreach ($free as $d=>$list) {
            if ($d<$first || $d>$last) continue;
            foreach ($list as $x) {$a=substr($x[0],0,5);if (($from!=='' && $a<substr($from,0,5)) || ($to!=='' && $a>=substr($to,0,5)) || in_array($d.' '.$a,$seen,true)) continue;$times[]=$a;$seen[]=$d.' '.$a;}
            if ($times) {$when=$d;break;}
        }
        if (!$times) continue;
        $dt=new DateTime($when.' '.$times[0],$tz);$card=tdbs_card((string)($e['contact_id']??''));
        $d=(array)($e['data']??array());
        $info=array('first_name'=>(string)($d['first_name']??''),'email'=>(string)($d['email']??''),'notices'=>!($card && array_key_exists('notices',$card) && !$card['notices']),
            'service'=>(string)($sv['name']??''),'location'=>(string)($sv['location']??''),'operator'=>'','start'=>$dt->getTimestamp());
        $quando=TDBS_DAYS[(int)$dt->format('N')-1].' '.$dt->format('d/m/Y').' alle '.implode(', ',array_slice($times,0,6)).(count($times)>6?' e altri orari':'');
        if (tdbs_tell('waitlist',$row['ref'],$info,array('quando'=>$quando,'nota'=>'Lo abbiamo scritto a tutti quelli in lista: lo prende chi prenota per primo. Se non c’è più, sulla pagina vedi cosa resta libero.'))) $sent++;
        tdbs_state_put($row['ref'],'waiting',$dt->getTimestamp(),array('contact_id'=>(string)($e['contact_id']??''),'service_id'=>(string)($e['service_id']??'')),array('seen'=>array_slice($seen,-300)));
    }
    return $sent;
}
/** A flag set after since on another row of the same client (the state keeps cs = client.service). */
function tdbs_told_lately($info,$flag,$since) {
    global $wpdb;list($state)=tdbs_mail_tables();$contact=(string)($info['contact_id']??'');
    if ($contact==='') return false;
    foreach ((array)$wpdb->get_col($wpdb->prepare("SELECT flags FROM $state WHERE cs LIKE %s AND start>%d",$wpdb->esc_like($contact).'.%',$since-30*DAY_IN_SECONDS)) as $f) {
        $f=json_decode((string)$f,true);if (is_array($f) && (int)($f[$flag]??0)>$since) return true;
    }
    return false;
}

/* ---------- the client's page of the links: the booking, Confermo, Annulla (Non vengo inside the cutoff) */
add_action('template_redirect','tdbs_link_page',1);
function tdbs_link_page() {
    if (!isset($_GET['tdbs'])) return;
    $token=(string)wp_unslash($_GET['tdbs']);
    if ($token==='book') {$page=tdbs_booking_page(false);wp_safe_redirect($page?get_permalink($page):home_url('/'));exit;}
    nocache_headers();header('X-Robots-Tag: noindex, nofollow');
    $d=tdbs_link_open($token);$b=$d?tdbs_state_get($d['r']):null;
    if (!$d || !$b) tdbs_page('Collegamento scaduto','Il collegamento non è più valido. Contattaci per assistenza.');
    $v=tdbs_values($b['info'],$b['ref']);$info=$b['info'];
    $what=esc_html($v['servizio']).'<br>'.esc_html(ucfirst($v['quando'])).($v['operatore']!==''?'<br>Con '.esc_html($v['operatore']):'').($v['sede']!==''?'<br>'.esc_html($v['sede']):'');
    $sv=tdbs_pc_service(tdbs_pc_snapshot(),(string)($info['service_id']??''));$cut=(int)($sv['cutoff']??0);$phone=(string)($sv['phone']??(tdbs_pc_snapshot()['phone']??''));
    $call=$phone!==''?' Chiamaci al '.esc_html($phone).'.':' Contattaci.';
    if ($b['status']==='cancelled' || !empty($b['flags']['cancelled'])) tdbs_page('Prenotazione annullata',$what.'<p>Questa prenotazione è annullata.</p>');
    if ((int)$b['start']<=time() || !in_array($b['status'],array('pending','confirmed'),true)) tdbs_page('Prenotazione',$what.'<p>La prenotazione è già iniziata o conclusa.'.$call.'</p>');
    $late=$cut && time()>(int)$b['start']-$cut*60;
    if ($_SERVER['REQUEST_METHOD']==='POST') {
        $act=(string)($_POST['do']??'');
        if (!tdbs_pc_throttle('tdbs_rll',30,600)) tdbs_page('Troppe richieste','Riprova tra un’ora.');
        if ($act==='confirm') {
            $b['flags']['confirmed']=time();unset($b['flags']['declined']);tdbs_state_put($b['ref'],$b['status'],$b['start'],$info,$b['flags']);tdbs_change($b['ref'],'confirm');
            tdbs_page('Grazie, ti aspettiamo',$what.'<p>Abbiamo detto all’attività che ci sarai.</p>');
        }
        if ($act==='cancel' && $late) {
            $b['flags']['declined']=time();tdbs_state_put($b['ref'],$b['status'],$b['start'],$info,$b['flags']);tdbs_change($b['ref'],'declined');
            tdbs_notify('Il cliente non viene (oltre il termine per annullare)',$info);
            tdbs_page('Abbiamo avvisato l’attività',$what.'<p>Il termine per annullare online è scaduto, quindi la prenotazione la annulla l’attività.'.$call.'</p>');
        }
        if ($act==='cancel') {
            global $wpdb;
            $b['flags']['cancelled']=time();tdbs_state_put($b['ref'],'cancelled',$b['start'],$info,$b['flags']);
            // made here and not yet taken by the PC: gone from the site's requests, its time free again here
            if (preg_match('/^wp-([a-f0-9]{32})$/',$b['alias']?:$b['ref'],$m)) $wpdb->delete(tdbs_pc_table(),array('id'=>$m[1]));
            tdbs_change($b['ref'],'cancel');
            tdbs_tell('cancelled',$b['ref'],$info);tdbs_notify('Prenotazione annullata dal cliente',$info);
            tdbs_page('Prenotazione annullata',$what.'<p>Grazie di averci avvisato: il posto torna libero per altri.</p>');
        }
    }
    $form=function($act,$label,$primary)use($token){return '<form method="post" style="display:inline"><input type="hidden" name="tdbs" value="'.esc_attr($token).'"><input type="hidden" name="do" value="'.$act.'"><button type="submit" class="'.($primary?'primary':'').'">'.esc_html($label).'</button></form> ';};
    $buttons=(empty($b['flags']['confirmed'])&&$b['status']==='confirmed'?$form('confirm','Confermo, ci sarò',($d['k']??'')==='confirm'):'')
        .$form('cancel',$late?'Non vengo':'Annulla la prenotazione',false);
    tdbs_page($b['status']==='pending'?'La tua richiesta':'La tua prenotazione',$what.($b['status']==='pending'?'<p>In attesa della conferma dell’attività.</p>':'').(!empty($b['flags']['confirmed'])?'<p>Hai confermato che ci sarai.</p>':'')
        .($late?'<p>Il termine per annullare online è scaduto: con «Non vengo» avvisi l’attività.</p>':'').'<p>'.$buttons.'</p>');
}
function tdbs_page($title,$html) {
    $snap=tdbs_pc_snapshot();$name=(string)($snap['name']??get_bloginfo('name'));
    $color=preg_match('/^#[0-9a-fA-F]{6}$/',(string)($snap['color']??''))?$snap['color']:'#6d4aff';
    status_header(200);header('Content-Type: text/html; charset=utf-8');
    echo '<!DOCTYPE html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>'.esc_html($title.' · '.$name).'</title>'
        .'<style>body{margin:0;padding:32px 16px;background:#f5f4f7;font:16px/1.6 Arial,Helvetica,sans-serif;color:#222}main{max-width:560px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid '.$color.';padding:24px 28px}'
        .'h1{font-size:22px;margin:.2em 0 .8em}.name{color:'.$color.';font-weight:bold}button{font:inherit;font-weight:bold;padding:11px 22px;border-radius:8px;border:1px solid '.$color.';background:#fff;color:'.$color.';cursor:pointer;margin:0 8px 8px 0}button.primary{background:'.$color.';color:#fff}</style></head>'
        .'<body><main><div class="name">'.esc_html($name).'</div><h1>'.esc_html($title).'</h1>'.$html.'</main></body></html>';
    exit;
}

/* ---------- the settings: Email ai clienti */
function tdbs_mail_settings($post) {
    $c=tdbs_mail_conf();$on=tdbs_master();$err=get_option('tdbs_mail_error');$last=(int)get_option('tdbs_mail_last',0);
    echo '<div class="card" style="max-width:860px"><h2 id="tdbs-email">Email ai clienti</h2>';
    echo $on?'<p><strong>Attive.</strong> Il programma ha acceso <em>Il sito gestisce prenotazioni ed email</em>: le email ai clienti e gli avvisi a voi li manda questo sito, anche con il PC spento. Il programma non ne manda più. Anche Prima dell’arrivo, Dopo la visita, Richiamo e Si è liberato un posto le decide e le manda questo sito, dall’archivio e dalla lista d’attesa che il PC sincronizza.</p>'
        :'<p><strong>Spente.</strong> Si accendono dal programma: <em>Impostazioni e sito › Sito WordPress › Modalità PC</em>, con <em>Sito come archivio centrale</em> e <em>Il sito gestisce prenotazioni ed email</em>. Finché sono spente le email le manda il programma.</p>';
    echo '<p class="description">Partono con la posta di questo sito (wp_mail): con il plugin SMTP che usate per i moduli, purché lo usi per tutte le email di WordPress. Ultimo invio riuscito: '.($last?esc_html(wp_date('d/m/Y H:i',$last)):'nessuno').'.'
        .(is_array($err)?' <strong>Ultimo invio non riuscito: '.esc_html(wp_date('d/m/Y H:i',(int)$err['at'])).', a '.esc_html((string)$err['to']).'. Controlla l’SMTP del sito.</strong>':'').'</p>';
    echo '<p class="description">I promemoria partono da WP-Cron, che gira quando qualcuno apre il sito. Per averli all’ora giusta anche di notte aggiungi nel server un cron ogni 5 minuti: <code>*/5 * * * * wget -q -O - '.esc_html(site_url('wp-cron.php?doing_wp_cron')).' &gt;/dev/null 2&gt;&amp;1</code> (in aaPanel: Cron › Accedi a URL).</p>';
    echo '<form method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_mail_save">';wp_nonce_field('tdbs_mail_save');
    echo '<table class="form-table"><tr><th><label for="tdbs-notify">Avvisi a voi</label></th><td><input type="email" class="regular-text" id="tdbs-notify" name="notify" value="'.esc_attr($c['notify']).'" placeholder="titolare@esempio.it"><p class="description">Una prenotazione fatta dal sito, annullata da un cliente o con «Non vengo». Vuoto: nessun avviso.</p></td></tr>';
    echo '<tr><th>Campi</th><td><p class="description"><code>{nome}</code> nome del cliente, <code>{servizio}</code>, <code>{quando}</code> giorno e ora, <code>{data}</code>, <code>{ora}</code>, <code>{sede}</code>, <code>{operatore}</code>, <code>{attivita}</code>, <code>{link}</code> la pagina per gestire o annullare, <code>{motivo}</code> (solo in «Non registrata»).</p></td></tr>';
    foreach (TDBS_MAIL_KINDS as $k=>$label) {
        $x=$c[$k];$id='tdbs-'.$k;
        echo '<tr><th><label for="'.$id.'-s">'.esc_html($label[0]).'</label></th><td><p class="description">'.esc_html($label[1]).'</p>';
        if (array_key_exists('on',$x)) echo '<p><label><input type="checkbox" name="'.$k.'[on]" value="1"'.checked(!empty($x['on']),true,false).'> Parte</label>'.(array_key_exists('hours',$x)?' · <label>ore '.($k==='review'?'dopo':'prima').' <input type="number" min="1" max="168" name="'.$k.'[hours]" value="'.esc_attr((string)$x['hours']).'" style="width:5em"></label>':'').'</p>';
        if (array_key_exists('url',$x)) echo '<p><label>Link per la recensione <input type="url" class="regular-text" name="'.$k.'[url]" value="'.esc_attr($x['url']).'" maxlength="500" placeholder="https://g.page/r/…/review"></label> <span class="description">per esempio quello di Google: profilo dell’attività › Chiedi recensioni. Senza link non parte</span></p>';
        echo '<p><input type="text" class="large-text" id="'.$id.'-s" name="'.$k.'[subject]" value="'.esc_attr($x['subject']).'" maxlength="150"></p><p><textarea class="large-text" rows="4" name="'.$k.'[body]" maxlength="4000">'.esc_textarea($x['body']).'</textarea></p></td></tr>';
    }
    echo '</table>';submit_button('Salva le email','secondary');echo '</form>';
    echo '<form method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_mail_test">';wp_nonce_field('tdbs_mail_test');
    echo '<p><label>Prova a <input type="email" name="to" class="regular-text" required placeholder="tuo@indirizzo.it"></label> ';submit_button('Manda un’email di prova','secondary','submit',false);echo '</p></form></div>';
}
add_action('admin_post_tdbs_mail_save',function(){
    tdbs_admin_check('tdbs_mail_save');
    $d=tdbs_mail_defaults();$out=array('notify'=>'');
    $notify=sanitize_email(wp_unslash($_POST['notify']??''));
    if ($notify!=='' && !is_email($notify)) {tdbs_flash('Avvisi a voi: l’email non è valida.');tdbs_back();}
    $out['notify']=$notify;
    foreach (array_keys(TDBS_MAIL_KINDS) as $k) {
        $x=(array)wp_unslash($_POST[$k]??array());
        $subject=trim(sanitize_text_field((string)($x['subject']??'')));$body=trim(sanitize_textarea_field((string)($x['body']??'')));
        $out[$k]=array('subject'=>mb_substr($subject!==''?$subject:$d[$k]['subject'],0,150),'body'=>mb_substr($body!==''?$body:$d[$k]['body'],0,4000));
        if (array_key_exists('on',$d[$k])) $out[$k]['on']=!empty($x['on']);
        if (array_key_exists('hours',$d[$k])) $out[$k]['hours']=max(1,min(168,absint($x['hours']??$d[$k]['hours'])));
        if (array_key_exists('url',$d[$k])) {$url=esc_url_raw(trim((string)($x['url']??'')));if ($url!=='' && !preg_match('#^https?://#',$url)) {tdbs_flash('Dopo la visita: il link della recensione non è valido.');tdbs_back();}$out[$k]['url']=mb_substr($url,0,500);}
    }
    update_option('tdbs_mail',$out,false);
    tdbs_flash('Email ai clienti salvate.','success');tdbs_back();
});
add_action('admin_post_tdbs_mail_test',function(){
    tdbs_admin_check('tdbs_mail_test');
    $to=sanitize_email(wp_unslash($_POST['to']??''));
    if (!is_email($to)) {tdbs_flash('Scrivi un indirizzo email valido.');tdbs_back();}
    $ok=tdbs_send($to,'Prova · '.(string)(tdbs_pc_snapshot()['name']??get_bloginfo('name')),"Se leggi questa email, le email ai clienti partono da questo sito.");
    tdbs_flash($ok?'Email di prova mandata a '.$to.'.':'Email non partita: controlla l’SMTP del sito.',$ok?'success':'error');tdbs_back();
});
