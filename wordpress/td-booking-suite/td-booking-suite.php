<?php
/**
 * Plugin Name: TD Booking Suite — Prenotazioni
 * Description: Mostra nel sito il modulo di prenotazione di TD Booking Suite. Due modi: collegato al portale con un codice, e si configura da solo; oppure in modalità PC, con il programma su un PC spesso spento: il modulo usa i dati che il PC manda e le richieste restano cifrate nel sito solo finché il PC non le prende. Con una password, [tdbs_agenda] mostra al titolare e ai collaboratori le prenotazioni mandate cifrate dal PC e può aggiungere appuntamenti, che il PC prende alla sincronizzazione. Il sito può fare da archivio centrale cifrato del programma, da cui un PC nuovo riprende tutto: di ogni record tiene la versione modificata per ultima. Può anche gestire prenotazioni ed email: conferma subito chi prenota dal sito e manda conferme, promemoria, modifiche e annullamenti anche con il PC spento.
 * Version: 2.21.0
 * Requires at least: 6.2
 * Requires PHP: 8.0
 * Author: Tastiere Digitali
 * License: GPL-2.0-or-later
 */
defined('ABSPATH') || exit;

const TDBS_VERSION='2.21.0';

function tdbs_settings() {
    $s=get_option('tdbs_settings',array());
    return is_array($s)?$s:array();
}
function tdbs_connected() {
    $s=tdbs_settings();
    return !empty($s['origin']) && !empty($s['tenant']);
}

add_action('admin_menu',function(){add_options_page('TD Booking Suite','TD Booking Suite','manage_options','tdbooking','tdbs_settings_page');});
add_action('admin_init',function(){register_setting('tdbooking','tdbs_settings',array('type'=>'array','sanitize_callback'=>'tdbs_sanitize'));});

/** Only for a local test portal: define('TDBS_ALLOW_HTTP',true) in wp-config.php. Real portals are HTTPS. */
function tdbs_schemes() {return defined('TDBS_ALLOW_HTTP') && TDBS_ALLOW_HTTP ? array('https','http') : array('https');}
function tdbs_clean_origin($value) {
    $origin=rtrim(esc_url_raw(trim((string)$value),tdbs_schemes()),'/');
    $url=wp_parse_url($origin);
    if (!$origin || empty($url['host']) || !in_array($url['scheme']??'',tdbs_schemes(),true) || !empty($url['path']) || !empty($url['query']) || !empty($url['user']) || !empty($url['fragment'])) return '';
    // the browser reports origins in lower case: the frame resizer compares them as they are
    return strtolower($origin);
}
/** What the portal sent: names and ids of locations and services, nothing personal. */
function tdbs_clean_profile($data) {
    $out=array('name'=>sanitize_text_field((string)($data['name']??'')),'business_label'=>sanitize_text_field((string)($data['business_label']??'')),'locations'=>array(),'services'=>array(),'updated'=>gmdate('c'));
    foreach (array_slice((array)($data['locations']??array()),0,50) as $l) {
        $id=(string)($l['id']??'');if (!preg_match('/^[a-f0-9-]{36}$/i',$id)) continue;
        $out['locations'][]=array('id'=>$id,'name'=>sanitize_text_field((string)($l['name']??'')));
    }
    foreach (array_slice((array)($data['services']??array()),0,200) as $s) {
        $id=(string)($s['id']??'');if (!preg_match('/^[a-f0-9-]{36}$/i',$id)) continue;
        $out['services'][]=array('id'=>$id,'name'=>sanitize_text_field((string)($s['name']??'')),'location_name'=>sanitize_text_field((string)($s['location_name']??'')));
    }
    return $out;
}
function tdbs_sanitize($value) {
    $old=tdbs_settings();
    if (!is_array($value)) return $old;
    // disconnected: the Prenota page is remembered, so connecting again does not publish a second one
    if (!empty($value['disconnected'])) return !empty($old['page'])?array('page'=>absint($old['page'])):array();
    $origin=tdbs_clean_origin($value['origin']??'');
    if (!$origin) {add_settings_error('tdbooking','origin','Indica l’indirizzo del portale come https://dominio, senza percorso.');return $old;}
    $slug=sanitize_title($value['tenant']??'');
    if (!$slug || strlen($slug)>60) {add_settings_error('tdbooking','tenant','Indirizzo breve non valido.');return $old;}
    $out=array('origin'=>$origin,'tenant'=>$slug);
    // the connection data survive a save of the manual form, unless the portal or the business changed
    $same=($old['origin']??'')===$origin && ($old['tenant']??'')===$slug;
    if (isset($value['profile']) && is_array($value['profile'])) $out['profile']=tdbs_clean_profile($value['profile']);
    elseif ($same && !empty($old['profile'])) $out['profile']=$old['profile'];
    if (!empty($value['page'])) $out['page']=absint($value['page']);
    elseif (!empty($old['page'])) $out['page']=absint($old['page']);
    return $out;
}

/* ---------- Collegati al portale */
function tdbs_back() {wp_safe_redirect(admin_url('options-general.php?page=tdbooking'));exit;}
function tdbs_remote_error($response) {
    if (is_wp_error($response)) return 'Portale non raggiungibile: '.$response->get_error_message();
    $data=json_decode((string)wp_remote_retrieve_body($response),true);
    if (is_array($data) && !empty($data['error'])) return (string)$data['error'];
    return 'Il portale ha risposto con un errore ('.(int)wp_remote_retrieve_response_code($response).'). Controlla l’indirizzo del portale.';
}
function tdbs_flash($message,$type='error') {set_transient('tdbs_notice_'.get_current_user_id(),array('type'=>$type,'text'=>$message),120);}
function tdbs_notice() {
    $key='tdbs_notice_'.get_current_user_id();$n=get_transient($key);
    if (!is_array($n)) return;
    delete_transient($key);
    echo '<div class="notice notice-'.($n['type']==='success'?'success':'error').' is-dismissible"><p>'.esc_html($n['text']).'</p></div>';
}
function tdbs_site_origin() {
    $u=wp_parse_url(home_url());
    return ($u['scheme']??'https').'://'.$u['host'].(!empty($u['port'])?':'.$u['port']:'');
}
function tdbs_booking_page($create) {
    $s=tdbs_settings();
    if (!empty($s['page']) && !in_array(get_post_status((int)$s['page']),array(false,'trash'),true)) return (int)$s['page'];
    if (!$create) return 0;
    $id=wp_insert_post(array('post_title'=>'Prenota','post_name'=>'prenota','post_status'=>'publish','post_type'=>'page','post_content'=>'<!-- wp:shortcode -->[td_booking_suite]<!-- /wp:shortcode -->'),true);
    return is_wp_error($id)?0:(int)$id;
}
function tdbs_admin_check($action) {
    if (!current_user_can('manage_options')) wp_die('Accesso non consentito.',403);
    check_admin_referer($action);
}
add_action('admin_post_tdbs_connect',function(){
    tdbs_admin_check('tdbs_connect');
    $origin=tdbs_clean_origin(wp_unslash($_POST['portal']??''));
    $code=strtoupper(preg_replace('/[^A-Za-z0-9]/','',(string)wp_unslash($_POST['code']??'')));
    if (!$origin) {tdbs_flash('Indirizzo del portale non valido: scrivilo come https://portale.tuodominio.it');tdbs_back();}
    if (strlen($code)!==16) {tdbs_flash('Il codice di collegamento ha 16 caratteri: copialo dal portale.');tdbs_back();}
    $response=wp_remote_post($origin.'/api/wordpress/connect/',array('timeout'=>20,'redirection'=>0,'headers'=>array('Content-Type'=>'application/json','Accept'=>'application/json'),
        'body'=>wp_json_encode(array('code'=>$code,'site'=>tdbs_site_origin(),'plugin'=>TDBS_VERSION))));
    $data=is_wp_error($response)?null:json_decode((string)wp_remote_retrieve_body($response),true);
    if (wp_remote_retrieve_response_code($response)!==200 || !is_array($data) || empty($data['ok']) || empty($data['tenant'])) {tdbs_flash(tdbs_remote_error($response));tdbs_back();}
    update_option('tdbs_settings',array('origin'=>$origin,'tenant'=>sanitize_title($data['tenant']),'profile'=>$data));
    $page=tdbs_booking_page(!empty($_POST['create_page']));
    if ($page) {$settings=tdbs_settings();$settings['page']=$page;update_option('tdbs_settings',$settings);}
    tdbs_flash('Collegato a '.sanitize_text_field((string)($data['name']??$data['tenant'])).': il sito è autorizzato e la configurazione è pronta.'.($page?' La pagina Prenota è pubblicata.':''),'success');
    tdbs_back();
});
add_action('admin_post_tdbs_refresh',function(){
    tdbs_admin_check('tdbs_refresh');
    $s=tdbs_settings();
    if (!tdbs_connected()) tdbs_back();
    $response=wp_remote_get($s['origin'].'/book/'.rawurlencode($s['tenant']).'/api/config/',array('timeout'=>20,'redirection'=>0,'headers'=>array('Accept'=>'application/json')));
    $data=is_wp_error($response)?null:json_decode((string)wp_remote_retrieve_body($response),true);
    if (wp_remote_retrieve_response_code($response)!==200 || !is_array($data) || empty($data['ok'])) {tdbs_flash(tdbs_remote_error($response));tdbs_back();}
    $s['profile']=$data;update_option('tdbs_settings',$s);
    tdbs_flash('Configurazione aggiornata dal portale.','success');tdbs_back();
});
add_action('admin_post_tdbs_disconnect',function(){
    tdbs_admin_check('tdbs_disconnect');
    update_option('tdbs_settings',array('disconnected'=>true));
    tdbs_flash('Sito scollegato. Per togliere anche l’autorizzazione, scollegalo dal portale in Configura l’attività.','success');tdbs_back();
});

function tdbs_settings_page() {
    if (!current_user_can('manage_options')) return;
    $s=tdbs_settings();$connected=tdbs_connected();$post=esc_url(admin_url('admin-post.php'));
    echo '<div class="wrap"><h1>TD Booking Suite · Prenotazioni</h1>';
    tdbs_notice();
    echo '<div class="card" style="max-width:860px"><h2>Come funziona</h2><p>Il plugin mostra nel sito il modulo di prenotazione, con lo shortcode <code>[td_booking_suite]</code> in una pagina. Si usa in uno dei due modi, secondo dove sta il programma:</p><ul style="list-style:disc;padding-left:20px">'
        .'<li><strong>Online, collegato al portale</strong>: il programma è su Internet, sempre acceso (Cloud o server dell’agenzia). Usa il riquadro <em>Collegati al portale</em>.</li>'
        .'<li><strong>Modalità PC</strong>: il programma è su un PC dell’attività, che spesso è spento (edizione Windows). Usa il riquadro <em>Modalità PC</em> più in basso, che ha anche l’agenda riservata.</li></ul><p>Nel dubbio chiedi a chi ti ha installato il programma.</p></div>';
    if ($connected) {
        $p=is_array($s['profile']??null)?$s['profile']:array();$many=count($p['locations']??array())>1;
        echo '<div class="card" style="max-width:860px"><h2>Collegato a '.esc_html($p['name']??$s['tenant']).'</h2><p>Portale <code>'.esc_html($s['origin']).'</code>, attività <code>'.esc_html($s['tenant']).'</code>'.(!empty($p['business_label'])?', '.esc_html($p['business_label']):'').'.</p>';
        $page=tdbs_booking_page(false);
        if ($page) echo '<p>Pagina con il modulo: <a href="'.esc_url(get_permalink($page)).'" target="_blank" rel="noopener">'.esc_html(get_the_title($page)).'</a> · <a href="'.esc_url(get_edit_post_link($page)).'">modifica</a></p>';
        echo '<h3>Shortcode da usare</h3><table class="widefat striped"><tbody><tr><td>Tutte le prenotazioni</td><td><code>[td_booking_suite]</code></td></tr>';
        if ($many) foreach ($p['locations'] as $l) echo '<tr><td>Solo la sede '.esc_html($l['name']).'</td><td><code>[td_booking_suite location="'.esc_attr($l['id']).'"]</code></td></tr>';
        foreach (($p['services']??array()) as $sv) echo '<tr><td>Servizio '.esc_html($sv['name']).($many?' · '.esc_html($sv['location_name']):'').'</td><td><code>[td_booking_suite service="'.esc_attr($sv['id']).'"]</code></td></tr>';
        echo '</tbody></table><p class="description">Tema facoltativo: <code>theme="dark"</code> o <code>theme="light"</code>. Lingua: da sola quella del browser del visitatore, oppure fissa con <code>lang="en"</code> o <code>lang="it"</code>. Il modulo legge sedi, servizi e orari dal portale in tempo reale: se l’attività cambia, il sito si aggiorna da solo. Aggiorna qui solo per rivedere l’elenco degli shortcode.</p><p>';
        echo '<form method="post" action="'.$post.'" style="display:inline"><input type="hidden" name="action" value="tdbs_refresh">';wp_nonce_field('tdbs_refresh');submit_button('Aggiorna la configurazione','secondary','submit',false);echo '</form> ';
        echo '<form method="post" action="'.$post.'" style="display:inline" onsubmit="return confirm(\'Scollegare il sito dal portale?\')"><input type="hidden" name="action" value="tdbs_disconnect">';wp_nonce_field('tdbs_disconnect');submit_button('Scollega','delete','submit',false);echo '</form></p></div>';
    }
    echo '<div class="card" style="max-width:860px"><h2>'.($connected?'Collega a un’altra attività':'Collegati al portale').'</h2><ol><li>Nel portale apri <em>Configura l’attività › Sito WordPress</em> e premi <em>Genera il codice di collegamento</em>: vale 24 ore e una volta sola.</li><li>Qui sotto scrivi l’indirizzo del portale, quello che apri nel browser per entrare, e incolla il codice.</li><li>Premi <em>'.($connected?'Collega':'Collegati').'</em>: il plugin autorizza questo sito, legge sedi e servizi e, se lasci la spunta, crea la pagina Prenota con il modulo.</li><li>Per una sola sede o un solo servizio usa gli shortcode che compaiono qui dopo il collegamento.</li></ol>';
    echo '<form method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_connect">';wp_nonce_field('tdbs_connect');
    echo '<table class="form-table"><tr><th><label for="tdbs-portal">Indirizzo del portale</label></th><td><input type="url" class="regular-text" id="tdbs-portal" name="portal" value="'.esc_attr($s['origin']??'').'" required placeholder="https://portale.tuodominio.it"></td></tr>';
    echo '<tr><th><label for="tdbs-code">Codice di collegamento</label></th><td><input class="regular-text" id="tdbs-code" name="code" required autocomplete="off" placeholder="ABCD-EFGH-JKLM-NPQR" style="text-transform:uppercase;letter-spacing:.05em"></td></tr>';
    if (!tdbs_booking_page(false)) echo '<tr><th>Pagina</th><td><label><input type="checkbox" name="create_page" value="1" checked> Crea la pagina Prenota con il modulo</label></td></tr>';
    echo '</table>';submit_button($connected?'Collega':'Collegati','primary','submit',false);echo '</form></div>';
    echo '<details style="max-width:860px;margin-top:20px"><summary style="cursor:pointer;font-weight:600">Impostazioni manuali</summary><p>In alternativa al codice: inserisci il portale e l’indirizzo breve assegnati dall’amministratore, e nel portale fai autorizzare questo sito: <code>'.esc_html(tdbs_site_origin()).'</code>.</p><form method="post" action="options.php">';
    settings_fields('tdbooking');
    echo '<table class="form-table"><tr><th><label for="tdbs-origin">Indirizzo del portale</label></th><td><input type="url" class="regular-text" id="tdbs-origin" name="tdbs_settings[origin]" value="'.esc_attr($s['origin']??'').'" required placeholder="https://portale.tuodominio.it"></td></tr><tr><th><label for="tdbs-tenant">Indirizzo breve attività</label></th><td><input class="regular-text" id="tdbs-tenant" name="tdbs_settings[tenant]" value="'.esc_attr($s['tenant']??'').'" required></td></tr></table>';
    submit_button();
    echo '</form></details>';
    tdbs_pc_settings($post);
    tdbs_mail_settings($post);
    echo '<p>Collegato al portale, il plugin non conserva nomi, recapiti, note o credenziali nel database WordPress: il modulo arriva dal portale, dentro un riquadro della pagina. In modalità PC conserva le richieste cifrate solo finché il PC non le prende.</p></div>';
}

function tdbs_shortcode($atts,$content='',$tag='td_booking_suite') {
    if (tdbs_pc_on()) return tdbs_pc_shortcode($atts);  // 2.17.0: the settings choose: PC mode, otherwise the portal's frame
    $s=tdbs_settings();
    if (!tdbs_connected()) return '<p>Prenotazioni temporaneamente non disponibili. Contatta l’attività.</p>';
    $atts=shortcode_atts(array('theme'=>'auto','service'=>'','location'=>'','lang'=>''),$atts,$tag);
    $theme=in_array($atts['theme'],array('auto','light','dark'),true)?$atts['theme']:'auto';
    $src=$s['origin'].'/book/'.rawurlencode($s['tenant']).'/?theme='.$theme;
    $service=trim((string)$atts['service']);
    if (preg_match('/^[a-f0-9-]{36}$/i',$service)) $src.='&service='.rawurlencode($service);
    $place=trim((string)$atts['location']);
    if (preg_match('/^[a-f0-9-]{36}$/i',$place)) $src.='&location='.rawurlencode($place);
    // 2.14.0: the page in this language; without it, the visitor's browser chooses
    if (in_array($atts['lang'],array('it','en'),true)) $src.='&lang='.$atts['lang'];
    wp_enqueue_style('tdbs-widget',plugins_url('widget.css',__FILE__),array(),TDBS_VERSION);
    wp_enqueue_script('tdbs-widget',plugins_url('widget.js',__FILE__),array(),TDBS_VERSION,true);
    return '<div class="tdbs-embed"><iframe class="tdbs-frame" src="'.esc_url($src).'" data-origin="'.esc_attr($s['origin']).'" title="Prenota" width="100%" height="1300" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-top-navigation-by-user-activation"></iframe><p><a target="_blank" rel="noopener noreferrer" href="'.esc_url($src).'">Apri le prenotazioni in una nuova pagina</a></p></div>';
}
add_action('init',function(){add_shortcode('td_booking_suite','tdbs_shortcode');});

/* ---------- 2.17.0 Modalità PC: the program runs on a PC that is often off and not reachable from the Internet.
   The PC calls the site (POST /wp-json/tdbs/v1/sync, signed with the shared secret) with services, operators and free
   times; the site answers with the requests taken meanwhile and deletes each one when the PC acks it. */
function tdbs_pc() {$p=get_option('tdbs_pc',array());return is_array($p)?$p:array();}
function tdbs_pc_on() {$p=tdbs_pc();return !empty($p['on']) && !empty($p['secret']);}
function tdbs_pc_snapshot() {$s=get_option('tdbs_pc_snapshot',array());return is_array($s)?$s:array();}
function tdbs_pc_fresh() {
    $snap=tdbs_pc_snapshot();$days=max(1,(int)(tdbs_pc()['max_age']??7));
    return !empty($snap['services']) && time()-(int)($snap['received']??0)<=$days*DAY_IN_SECONDS ? $snap : null;
}
function tdbs_pc_table() {
    global $wpdb;$table=$wpdb->prefix.'tdbs_bookings';
    if (get_option('tdbs_pc_db')!=='1') {
        require_once ABSPATH.'wp-admin/includes/upgrade.php';
        dbDelta("CREATE TABLE $table (\n id char(32) NOT NULL,\n created datetime NOT NULL,\n slot text NOT NULL,\n data longtext NOT NULL,\n PRIMARY KEY  (id)\n) ".$wpdb->get_charset_collate().';');
        update_option('tdbs_pc_db','1');
    }
    return $table;
}
/** The client's data at rest: AES-256-GCM, key derived from the shared secret, never sent to the browser. */
function tdbs_pc_key($secret=null) {return hash_hkdf('sha256',(string)($secret??(tdbs_pc()['secret']??'')),32,'tdbs-wp-data');}
function tdbs_pc_seal($data,$secret=null) {
    $iv=random_bytes(12);$tag='';
    $ct=openssl_encrypt(wp_json_encode($data),'aes-256-gcm',tdbs_pc_key($secret),OPENSSL_RAW_DATA,$iv,$tag,'',16);
    return base64_encode($iv.$tag.$ct);
}
function tdbs_pc_open($value,$secret=null) {
    $raw=base64_decode((string)$value,true);
    if ($raw===false || strlen($raw)<29) return null;
    $plain=openssl_decrypt(substr($raw,28),'aes-256-gcm',tdbs_pc_key($secret),OPENSSL_RAW_DATA,substr($raw,0,12),substr($raw,12,16));
    return $plain===false?null:json_decode($plain,true);
}
function tdbs_pc_sign($ts,$body) {return hash_hmac('sha256',$ts.'.'.$body,(string)(tdbs_pc()['secret']??''));}
function tdbs_pc_error($message,$status=400,$extra=array()) {return new WP_REST_Response(array('ok'=>false,'error'=>$message)+$extra,$status);}

add_action('rest_api_init',function(){
    register_rest_route('tdbs/v1','/sync',array('methods'=>'POST','callback'=>'tdbs_pc_sync','permission_callback'=>'__return_true'));
    register_rest_route('tdbs/v1','/slots',array('methods'=>'GET','callback'=>'tdbs_pc_slots_api','permission_callback'=>'__return_true'));
    register_rest_route('tdbs/v1','/book',array('methods'=>'POST','callback'=>'tdbs_pc_book','permission_callback'=>'__return_true'));
    // 2.19.0: the private agenda's Nuovo appuntamento, from the browser with the agenda's session: a visitors' route, not the PC's
    register_rest_route('tdbs/v1','/agenda/book',array('methods'=>'POST','callback'=>'tdbs_agenda_book','permission_callback'=>'__return_true'));
    // 2.18.0: the PC's archive; with /sync the only routes of the PC, under one prefix a firewall can allow (/slots and /book are the visitors')
    register_rest_route('tdbs/v1','/archive/push',array('methods'=>'POST','callback'=>'tdbs_archive_push','permission_callback'=>'__return_true'));
    register_rest_route('tdbs/v1','/archive/pull',array('methods'=>'POST','callback'=>'tdbs_archive_pull','permission_callback'=>'__return_true'));
});

/** From the PC only: POST JSON, signature over "time.body", 5 minutes of window, each signature once. The body decoded, or the error. */
function tdbs_pc_verify(WP_REST_Request $request,$max) {
    if (empty(tdbs_pc()['secret'])) return tdbs_pc_error('Modalità PC non configurata.',404);
    $body=(string)$request->get_body();$ts=(string)$request->get_header('x-tdbs-time');$sig=(string)$request->get_header('x-tdbs-signature');
    $seen=get_option('tdbs_pc_seen',array());$seen=is_array($seen)?$seen:array();
    if (strlen($body)>$max) return tdbs_pc_error('Richiesta troppo grande.',413);
    if (!ctype_digit($ts) || abs(time()-(int)$ts)>300 || !hash_equals(tdbs_pc_sign($ts,$body),$sig) || in_array($sig,$seen,true)) return tdbs_pc_error('Firma non valida.',401);
    // 2.18.0: 500 kept, the archive's calls come many in a row
    update_option('tdbs_pc_seen',array_slice(array_merge($seen,array($sig)),-500),false);
    $data=json_decode($body,true);
    return is_array($data)?$data:tdbs_pc_error('Richiesta non valida.');
}
/** The answer is signed as a string inside the envelope: WordPress encodes the envelope as it likes. */
function tdbs_pc_reply($inner) {
    $inner=wp_json_encode($inner);$now=(string)time();
    $response=new WP_REST_Response(array('body'=>$inner,'time'=>$now,'sig'=>tdbs_pc_sign($now,$inner)),200);
    $response->header('Cache-Control','no-store');
    return $response;
}
function tdbs_pc_sync(WP_REST_Request $request) {
    global $wpdb;
    $data=tdbs_pc_verify($request,5000000);  // the free times of many services and days can be more than the archive's 512 KB
    if ($data instanceof WP_REST_Response) return $data;
    $table=tdbs_pc_table();
    foreach (array_slice((array)($data['ack']??array()),0,500) as $id) if (is_string($id) && preg_match('/^[a-f0-9]{32}$/',$id)) $wpdb->delete($table,array('id'=>$id));
    // without a snapshot nothing changed on the PC since the last one: the times are still those, checked now
    $snap=isset($data['snapshot']) && is_array($data['snapshot']) ? $data['snapshot'] : tdbs_pc_snapshot();
    if ($snap) {$snap['received']=time();update_option('tdbs_pc_snapshot',$snap,false);}
    // 2.17.1: the private agenda, sealed by the PC with the shared secret; kept only while it has a password here
    $agenda=get_option('tdbs_agenda',array());$agenda=is_array($agenda)?$agenda:array();
    if (isset($data['agenda']) && is_string($data['agenda'])) $agenda['sealed']=$data['agenda'];
    if (!empty(tdbs_pc()['agenda']) && !empty($agenda['sealed'])) {$agenda['received']=time();update_option('tdbs_agenda',$agenda,false);} else delete_option('tdbs_agenda');
    $out=array();
    foreach ((array)$wpdb->get_results("SELECT id,slot,data FROM $table ORDER BY created LIMIT 200",ARRAY_A) as $row) {
        $slot=json_decode($row['slot'],true);$client=tdbs_pc_open($row['data']);
        if (is_array($slot) && is_array($client)) $out[]=array_merge($slot,$client,array('id'=>$row['id']));
    }
    // 2.21.0: the site in charge: what the clients did on the links goes to the PC until it acks it; its bookings it could not keep, told to the client here
    list(,$changes)=tdbs_mail_tables();
    $ack=array_values(array_filter(array_slice((array)($data['changes_ack']??array()),0,500),function($x){return is_string($x) && ctype_digit($x);}));
    if ($ack) $wpdb->query('DELETE FROM '.$changes.' WHERE id IN ('.implode(',',array_map('intval',$ack)).')');
    foreach (array_slice((array)($data['refused']??array()),0,200) as $r) if (is_array($r) && preg_match('/^[a-f0-9]{32}$/',(string)($r['id']??''))) {
        $b=tdbs_state_get('wp-'.$r['id']);
        if (!$b || !empty($b['flags']['told'])) continue;
        $b['flags']['told']=time();
        if (!empty($r['kept'])) {tdbs_state_put($b['ref'],'pending',$b['start'],$b['info'],$b['flags']);tdbs_tell('recheck',$b['ref'],$b['info']);}
        else {tdbs_state_put($b['ref'],'cancelled',$b['start'],$b['info'],$b['flags']);tdbs_tell('refused',$b['ref'],$b['info'],array('motivo'=>rtrim(sanitize_text_field((string)($r['reason']??'')),'.')));}
    }
    $pending=array();
    foreach ((array)$wpdb->get_results("SELECT id,ref,action,at FROM $changes ORDER BY id LIMIT 500",ARRAY_A) as $c) $pending[]=array('id'=>(string)$c['id'],'ref'=>$c['ref'],'action'=>$c['action'],'at'=>$c['at']);
    return tdbs_pc_reply(array('ok'=>true,'bookings'=>$out,'changes'=>$pending));
}

/* ---------- 2.18.0 Il sito come archivio centrale: the PC pushes every change (bookings, client cards with fiscal data, client
   records, consent history, receipts' summary), each record sealed by the PC with the shared secret; the site keeps the last
   state of each, an empty one for what is gone, and gives it all back to a new PC. On only with the switch here too.
   2.20.0: each record comes with v, the moment of its last change in microseconds since 1970 UTC (the same in every time
   zone): the site keeps the higher v, on a tie the higher number; the rows saved before have v 0. */
const TDBS_ARCHIVE_BODY=524288;
function tdbs_archive_on() {$p=tdbs_pc();return !empty($p['archive']) && !empty($p['secret']);}
function tdbs_archive_table() {
    global $wpdb;$table=$wpdb->prefix.'tdbs_archive';
    // 2.20.0: '2' adds the column v to the table of 2.18.0 (dbDelta adds what is missing, the rows get v 0)
    if (get_option('tdbs_archive_db')!=='2') {
        require_once ABSPATH.'wp-admin/includes/upgrade.php';
        dbDelta("CREATE TABLE $table (\n kind varchar(10) NOT NULL,\n ref char(36) NOT NULL,\n seq bigint(20) unsigned NOT NULL,\n v bigint(20) unsigned NOT NULL DEFAULT 0,\n day char(10) NOT NULL DEFAULT '',\n data longtext NOT NULL,\n PRIMARY KEY  (kind,ref),\n KEY day (kind,day)\n) ".$wpdb->get_charset_collate().';');
        update_option('tdbs_archive_db','2');
    }
    return $table;
}
function tdbs_archive_status() {
    global $wpdb;$row=$wpdb->get_row("SELECT SUM(data<>'') AS records,COALESCE(SUM(LENGTH(data)),0) AS size FROM ".tdbs_archive_table(),ARRAY_A);
    return array('records'=>(int)($row['records']??0),'size'=>(int)($row['size']??0));
}
function tdbs_archive_push(WP_REST_Request $request) {
    global $wpdb;
    $data=tdbs_pc_verify($request,TDBS_ARCHIVE_BODY+4096);
    if ($data instanceof WP_REST_Response) return $data;
    if (!tdbs_archive_on()) return tdbs_pc_error('Nel plugin l’archivio è spento: accendilo in Impostazioni › TD Booking Suite.',409);
    $table=tdbs_archive_table();$acked=array();$bookings=array();
    // 2.20.0: the record changed last wins, by v (a PC before 2.20.0 sends none: 0, and the higher number wins as before);
    // the condition is read before seq and v change, v last of all
    $win='(VALUES(v)>v OR (VALUES(v)=v AND VALUES(seq)>seq))';
    foreach (array_slice((array)($data['ops']??array()),0,1000) as $op) {
        if (!is_array($op)) continue;
        $kind=(string)($op['kind']??'');$ref=(string)($op['ref']??'');$seq=$op['seq']??null;$v=$op['v']??0;$day=(string)($op['day']??'');$sealed=(string)($op['data']??'');
        if (!in_array($kind,array('booking','contact','treatment','consent','sale'),true) || !preg_match('/^[a-f0-9-]{36}$/',$ref) || !is_int($seq) || $seq<1 || !is_int($v) || $v<0 || ($day!=='' && !preg_match('/^\d{4}-\d{2}-\d{2}$/',$day)) || ($sealed!=='' && !preg_match('#^[A-Za-z0-9+/=]+$#',$sealed))) continue;
        // what is gone stays as an empty row with its v, so an older copy arriving late cannot bring it back; an older one is acked too, and kept out
        $done=$wpdb->query($wpdb->prepare("INSERT INTO $table (kind,ref,seq,v,day,data) VALUES (%s,%s,%d,%d,%s,%s) ON DUPLICATE KEY UPDATE data=IF($win,VALUES(data),data),day=IF($win,VALUES(day),day),seq=IF($win,VALUES(seq),seq),v=GREATEST(v,VALUES(v))",$kind,$ref,$seq,$v,$day,$sealed));
        if ($done!==false) $acked[]=$seq;
        if ($done!==false && $kind==='booking') $bookings[$ref]=array($v,$sealed);
    }
    // 2.21.0: after the cards (their "no" to the emails), what the site knows of each booking and the emails of what changed;
    // only the copy that won, and not the history of before yesterday
    if ($bookings && tdbs_master()) foreach ($bookings as $ref=>$x) {
        if ((int)$wpdb->get_var($wpdb->prepare("SELECT v FROM $table WHERE kind='booking' AND ref=%s",$ref))!==$x[0]) continue;
        $rec=$x[1]!==''?tdbs_pc_open($x[1]):null;
        if (!is_array($rec)) {$old=tdbs_state_get($ref);if ($old) tdbs_state_put($ref,'cancelled',$old['start'],$old['info'],$old['flags']);continue;}
        if (strtotime((string)($rec['start']??''))<time()-DAY_IN_SECONDS && !tdbs_state_get($ref)) continue;
        tdbs_state_from_archive($ref,$rec,$x[0]);
    }
    update_option('tdbs_archive_at',time(),false);
    return tdbs_pc_reply(array('ok'=>true,'acked'=>$acked)+tdbs_archive_status());
}
/** A page of one kind for a new PC, in order of id, about 400 KB at a time; next is where the following page starts.
    2.20.0: each with its v, not sealed; with gone also the empty rows of what is gone (Sincronizza con il sito). */
function tdbs_archive_pull(WP_REST_Request $request) {
    global $wpdb;
    $data=tdbs_pc_verify($request,TDBS_ARCHIVE_BODY);
    if ($data instanceof WP_REST_Response) return $data;
    if (!tdbs_archive_on()) return tdbs_pc_error('Nel plugin l’archivio è spento: accendilo in Impostazioni › TD Booking Suite.',409);
    $kind=in_array($data['kind']??'',array('booking','contact','treatment','consent','sale'),true)?$data['kind']:'contact';
    $after=preg_match('/^[a-f0-9-]{36}$/',(string)($data['after']??''))?(string)$data['after']:'';
    $gone=!empty($data['gone']);
    $rows=(array)$wpdb->get_results($wpdb->prepare('SELECT ref,v,data FROM '.tdbs_archive_table()." WHERE kind=%s AND ref>%s".($gone?'':" AND data<>''")." ORDER BY ref LIMIT 500",$kind,$after),ARRAY_A);
    $out=array();$size=0;
    foreach ($rows as $r) {$size+=strlen($r['data'])+90;if ($out && $size>400000) break;$out[]=array('ref'=>$r['ref'],'v'=>(int)$r['v'],'data'=>$r['data']);}
    $more=$out && (count($out)<count($rows) || count($rows)===500);
    return tdbs_pc_reply(array('ok'=>true,'records'=>$out,'next'=>$more?end($out)['ref']:''));
}
/** The bookings of the archive from one day to another, as the agenda shows them; their operators added to its columns. */
function tdbs_archive_bookings($from,$to,&$a) {
    global $wpdb;$out=array();$ids=array_map('strval',array_column((array)($a['operators']??array()),'id'));
    foreach ((array)$wpdb->get_results($wpdb->prepare('SELECT ref,data FROM '.tdbs_archive_table()." WHERE kind='booking' AND day BETWEEN %s AND %s AND data<>''",$from,$to),ARRAY_A) as $row) {
        $b=tdbs_pc_open($row['data']);$v=is_array($b)?($b['view']??null):null;
        if (!is_array($v)) continue;  // the cancelled ones have no view
        // 2.21.0: cancelled by the client from the site's link, the PC not told yet
        $st=tdbs_master()?tdbs_state_get($row['ref']):null;
        if ($st && !empty($st['flags']['cancelled'])) {$v['status']='Annullata dal cliente · da sincronizzare';$v['pending']=true;}
        elseif ($st && !empty($st['flags']['confirmed'])) $v['status'].=' · confermata dal cliente';
        $rid=(string)($v['resource']??'');
        if ($rid!=='' && !in_array($rid,$ids,true)) {$ids[]=$rid;$a['operators'][]=array('id'=>$rid,'name'=>(string)($v['operator']??''));}
        $out[]=$v;
    }
    return $out;
}

/** The site's requests the PC has not taken yet: their times are no longer free on the site. */
function tdbs_pc_taken() {
    global $wpdb;$out=array();
    foreach ((array)$wpdb->get_col('SELECT slot FROM '.tdbs_pc_table()) as $slot) {$slot=json_decode($slot,true);if (is_array($slot)) $out[]=$slot;}
    return $out;
}
function tdbs_pc_service($snap,$id) {
    foreach ((array)($snap['services']??array()) as $sv) if (is_array($sv) && ($sv['id']??'')===$id) return $sv;
    return null;
}
/** Free times per day, as array(day => array(array("HH:MM-HH:MM", resource id))): one resource, or the first free one
    of each time with $resource empty ("Il primo disponibile"); never past and never over a request of the site. */
function tdbs_pc_free($snap,$sv,$option,$resource) {
    try {$tz=new DateTimeZone((string)($snap['timezone']??'Europe/Rome'));} catch (Exception $e) {$tz=wp_timezone();}
    $now=new DateTime('now',$tz);$today=$now->format('Y-m-d');$clock=$now->format('H:i');
    $taken=tdbs_pc_taken();$out=array();
    $ids=$resource!==''?array($resource):array_column((array)($sv['resources']??array()),'id');
    foreach ($ids as $rid) foreach ((array)($sv['slots'][$option][$rid]??array()) as $day=>$ranges) {
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/',(string)$day) || $day<$today) continue;
        foreach ((array)$ranges as $range) {
            if (!preg_match('/^\d{2}:\d{2}-\d{2}:\d{2}$/',(string)$range)) continue;
            $a=substr($range,0,5);$b=substr($range,6,5);
            if ($day===$today && $a<=$clock) continue;
            foreach ($taken as $t) if (($t['resource']??'')===$rid && ($t['date']??'')===$day && $a<($t['end']??'') && $b>($t['time']??'')) continue 2;
            if (!isset($out[$day][$a])) $out[$day][$a]=array($range,$rid);
        }
    }
    ksort($out);
    foreach ($out as $day=>$times) {ksort($times);$out[$day]=array_values($times);}
    return $out;
}
function tdbs_pc_slots_api(WP_REST_Request $request) {
    $snap=tdbs_pc_on()?tdbs_pc_fresh():null;
    if (!$snap) return tdbs_pc_error('Prenotazioni online non disponibili.',404);
    $sv=tdbs_pc_service($snap,(string)$request->get_param('service'));
    if (!$sv) return tdbs_pc_error('Servizio non disponibile.',404);
    $response=new WP_REST_Response(array('ok'=>true,'days'=>(object)tdbs_pc_free($snap,$sv,(string)$request->get_param('option'),(string)$request->get_param('resource'))),200);
    $response->header('Cache-Control','no-store');
    return $response;
}
/** A few requests per address and per hour, and a ceiling for the whole site. 2.19.0: the agenda's own counters, higher. */
function tdbs_pc_throttle($name='tdbs_rl',$max=10,$ceiling=60) {
    $key=$name.'_'.md5((string)($_SERVER['REMOTE_ADDR']??'').wp_salt('nonce'));
    $one=(int)get_transient($key);$all=(int)get_transient($name.'_all');
    if ($one>=$max || $all>=$ceiling) return false;
    set_transient($key,$one+1,HOUR_IN_SECONDS);set_transient($name.'_all',$all+1,HOUR_IN_SECONDS);
    return true;
}
function tdbs_pc_book(WP_REST_Request $request) {
    global $wpdb;
    $p=$request->get_json_params();
    if (!is_array($p)) return tdbs_pc_error('Richiesta non valida.');
    if (!wp_verify_nonce((string)($p['nonce']??''),'tdbs_book')) return tdbs_pc_error('Pagina scaduta: ricaricala e riprova.',403);
    $snap=tdbs_pc_on()?tdbs_pc_fresh():null;
    if (!$snap) return tdbs_pc_error('Prenotazioni online non disponibili: contattaci.',404);
    if (!tdbs_pc_throttle()) return tdbs_pc_error('Troppe richieste: riprova tra un’ora.',429);
    $sv=tdbs_pc_service($snap,(string)($p['service']??''));
    if (!$sv) return tdbs_pc_error('Servizio non disponibile.');
    $option=(string)($p['option']??'');$options=array_column((array)($sv['options']??array()),'id');
    if ($options ? !in_array($option,$options,true) : $option!=='') return tdbs_pc_error('Scegli la prestazione.');
    $any=!empty($p['any']);$resource=(string)($p['resource']??'');
    if (!in_array($resource,array_column((array)($sv['resources']??array()),'id'),true)) return tdbs_pc_error('Scegli con chi.');
    $date=(string)($p['date']??'');$time=(string)($p['time']??'');
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/',$date) || !preg_match('/^\d{2}:\d{2}$/',$time)) return tdbs_pc_error('Scegli giorno e orario.');
    $client=array();
    foreach (array('first_name','last_name','phone') as $k) {
        $client[$k]=sanitize_text_field((string)($p[$k]??''));
        if ($client[$k]==='' || strlen($client[$k])>100) return tdbs_pc_error('Compila correttamente nome, cognome, email e telefono.');
    }
    $client['email']=strtolower(trim((string)($p['email']??'')));
    if (!is_email($client['email']) || strlen($client['email'])>254) return tdbs_pc_error('Email non valida.');
    if (!preg_match('/^[0-9 +().\/-]{6,30}$/',$client['phone'])) return tdbs_pc_error('Numero di telefono non valido, per esempio 333 123 4567.');
    $client['notes']=mb_substr(sanitize_textarea_field((string)($p['notes']??'')),0,4000);
    $client['notices']=!empty($p['notices']);$client['marketing']=!empty($p['marketing']);
    // 2.21.0: «mario rossi», «MARIO ROSSI» → «Mario Rossi»; as written when it has capitals of its own («Maria De Luca»)
    foreach (array('first_name','last_name') as $k) $client[$k]=tdbs_name_case($client[$k]);
    // one request at a time: two clients on the same time, only the first gets it
    $wpdb->get_var("SELECT GET_LOCK('tdbs_book',10)");
    $free=tdbs_pc_free($snap,$sv,$option,$any?'':$resource);$hit=null;
    foreach ((array)($free[$date]??array()) as $x) if (substr($x[0],0,5)===$time && ($any || $x[1]===$resource)) {$hit=$x;break;}
    if (!$hit) {$wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");return tdbs_pc_error('Questo orario non è più libero: scegline un altro.',409,array('reload'=>true));}
    $id=bin2hex(random_bytes(16));
    $saved=$wpdb->insert(tdbs_pc_table(),array('id'=>$id,'created'=>current_time('mysql',true),'slot'=>wp_json_encode(array('service'=>$sv['id'],'option'=>$option,'resource'=>$hit[1],'any'=>$any,'date'=>$date,'time'=>$time,'end'=>substr($hit[0],6,5))),'data'=>tdbs_pc_seal($client)));
    $wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");
    if (!$saved) return tdbs_pc_error('Non è stato possibile salvare la richiesta: riprova.',500);
    $who='';foreach ((array)($sv['resources']??array()) as $r) if (!$any && ($r['id']??'')===$hit[1]) $who=' con '.$r['name'];
    if (tdbs_master()) {
        // 2.21.0: the site in charge confirms it now (or takes the request, for a service the business confirms) and writes to the client
        $pending=!empty($sv['approval']);$info=tdbs_site_info($snap,$sv,$option,$any?'':$hit[1],$date,$time,substr($hit[0],6,5),$client);
        tdbs_state_put('wp-'.$id,$pending?'pending':'confirmed',$info['start'],$info,array(),time());
        tdbs_tell($pending?'requested':'confirmed','wp-'.$id,$info);
        tdbs_notify($pending?'Nuova richiesta dal sito':'Nuova prenotazione dal sito',$info);
        return new WP_REST_Response(array('ok'=>true,'message'=>$pending?'Richiesta ricevuta: ti scriviamo appena la confermiamo.':'Prenotazione confermata: ti abbiamo mandato un’email con i dettagli.'),201);
    }
    $day=DateTime::createFromFormat('Y-m-d',$date);
    wp_mail($client['email'],'Richiesta ricevuta · '.sanitize_text_field((string)($snap['name']??'')),
        'Ciao '.$client['first_name'].",\n\nabbiamo ricevuto la tua richiesta: ".sanitize_text_field((string)$sv['name']).$who.' il '.($day?$day->format('d/m/Y'):$date).' alle '.$time.".\nRiceverai la conferma per email.\n\n".sanitize_text_field((string)($snap['name']??'')));
    return new WP_REST_Response(array('ok'=>true,'message'=>'Richiesta ricevuta: riceverai la conferma per email.'),201);
}

function tdbs_pc_shortcode($atts) {
    $atts=shortcode_atts(array('service'=>'','theme'=>'auto','location'=>'','lang'=>''),$atts,'td_booking_suite');
    $snap=tdbs_pc_fresh();
    if (!$snap) {
        $phone=sanitize_text_field((string)(tdbs_pc_snapshot()['phone']??''));
        return '<p class="tdbs-pc-off">Le prenotazioni online non sono aggiornate in questo momento: contattaci'.($phone?' al <a href="tel:'.esc_attr(preg_replace('/[^0-9+]/','',$phone)).'">'.esc_html($phone).'</a>':'').'.</p>';
    }
    $services=array();
    foreach ((array)$snap['services'] as $sv) {
        if (!is_array($sv) || ($atts['service']!=='' && ($sv['id']??'')!==$atts['service'])) continue;
        $services[]=array('id'=>(string)$sv['id'],'name'=>(string)$sv['name'],'description'=>(string)($sv['description']??''),'picks'=>!empty($sv['picks']),
            'options'=>array_map(function($o){return array('id'=>(string)$o['id'],'name'=>(string)$o['name']);},(array)($sv['options']??array())),
            'resources'=>array_map(function($r){return array('id'=>(string)$r['id'],'name'=>(string)$r['name']);},(array)($sv['resources']??array())));
    }
    if (!$services) return '<p class="tdbs-pc-off">Nessun servizio prenotabile online: contattaci.</p>';
    $t=(array)($snap['texts']??array());
    wp_enqueue_style('tdbs-widget',plugins_url('widget.css',__FILE__),array(),TDBS_VERSION);
    wp_enqueue_script('tdbs-pc',plugins_url('pc.js',__FILE__),array(),TDBS_VERSION,true);
    $config=array('rest'=>esc_url_raw(rest_url('tdbs/v1/')),'nonce'=>wp_create_nonce('tdbs_book'),'services'=>$services);
    $field=function($name,$label,$type='text',$auto=''){return '<p><label>'.esc_html($label).'<br><input name="'.$name.'" type="'.$type.'" required maxlength="'.($type==='email'?254:100).'"'.($auto?' autocomplete="'.$auto.'"':'').'></label></p>';};
    return '<div class="tdbs-pc" data-config="'.esc_attr(wp_json_encode($config)).'"><form class="tdbs-pc-form" novalidate>'
        .'<p class="tdbs-pc-updated">Disponibilità aggiornata alle '.esc_html(wp_date('H:i',(int)$snap['received'])).' del '.esc_html(wp_date('d/m',(int)$snap['received'])).'</p>'
        .'<p><label>Servizio<br><select name="service" required></select></label><small class="tdbs-pc-description"></small></p>'
        .'<p class="tdbs-pc-option" hidden><label>Prestazione<br><select name="option"></select></label></p>'
        .'<p class="tdbs-pc-resource" hidden><label>Con chi<br><select name="resource"></select></label></p>'
        .'<p><label>Giorno<br><select name="date" required></select></label></p>'
        .'<div class="tdbs-pc-slots" role="group" aria-label="Orari disponibili"></div><p class="tdbs-pc-status" role="status" aria-live="polite"></p>'
        .'<fieldset><legend>I tuoi recapiti</legend>'.$field('first_name','Nome','text','given-name').$field('last_name','Cognome','text','family-name').$field('email','Email','email','email').$field('phone','Telefono','tel','tel').'</fieldset>'
        .'<p><label>Vuoi dirci qualcosa?<br><textarea name="notes" rows="3" maxlength="4000"></textarea></label></p>'
        .'<details><summary>Come utilizziamo i tuoi dati</summary><p>'.nl2br(esc_html((string)($t['privacy']??''))).'</p></details>'
        .'<p><label><input type="checkbox" name="notices" checked> '.esc_html((string)($t['notices']??'')).'</label></p>'
        .(!empty($t['consent'])?'<p><label><input type="checkbox" name="marketing"> '.esc_html((string)$t['consent']).' (facoltativo)</label></p>':'')
        .'<p><button type="submit">Invia la richiesta</button></p></form><div class="tdbs-pc-done" role="status" hidden></div></div>';
}

/* ---------- 2.17.1 Agenda riservata: the PC sends the bookings of the next days sealed with the shared secret; with the
   password set in the settings, [tdbs_agenda] shows them read-only, decrypted here. Who administers the site can read them. */
const TDBS_AGENDA_COOKIE='tdbs_agenda';
const TDBS_AGENDA_HOURS=8;
function tdbs_agenda_session() {
    $token=(string)($_COOKIE[TDBS_AGENDA_COOKIE]??'');$p=tdbs_pc();
    if (empty($p['agenda']) || !preg_match('/^[a-f0-9]{64}$/',$token)) return false;
    // a new password closes the sessions opened with the old one
    return get_transient('tdbs_ags_'.hash('sha256',$token))===(string)($p['agenda_ver']??'');
}
function tdbs_agenda_back($state='') {
    $back=wp_validate_redirect(esc_url_raw(wp_unslash($_POST['back']??'')),home_url('/'));
    wp_safe_redirect($state?add_query_arg('tdbs_agenda',$state,$back):remove_query_arg('tdbs_agenda',$back));exit;
}
function tdbs_agenda_login() {
    if (!wp_verify_nonce((string)($_POST['_wpnonce']??''),'tdbs_agenda')) tdbs_agenda_back('expired');
    $p=tdbs_pc();$ip='tdbs_agf_'.md5((string)($_SERVER['REMOTE_ADDR']??'').wp_salt('nonce'));$fails=(int)get_transient($ip);
    // 5 wrong passwords from an address, or 30 for the whole site, close the form for 15 minutes
    if ($fails>=5 || (int)get_transient('tdbs_agf_all')>=30) tdbs_agenda_back('locked');
    if (empty($p['agenda']) || !wp_check_password((string)wp_unslash($_POST['password']??''),(string)$p['agenda'])) {
        set_transient($ip,$fails+1,15*MINUTE_IN_SECONDS);set_transient('tdbs_agf_all',(int)get_transient('tdbs_agf_all')+1,15*MINUTE_IN_SECONDS);
        tdbs_agenda_back('wrong');
    }
    delete_transient($ip);$token=bin2hex(random_bytes(32));
    set_transient('tdbs_ags_'.hash('sha256',$token),(string)($p['agenda_ver']??''),TDBS_AGENDA_HOURS*HOUR_IN_SECONDS);
    setcookie(TDBS_AGENDA_COOKIE,$token,array('expires'=>time()+TDBS_AGENDA_HOURS*HOUR_IN_SECONDS,'path'=>COOKIEPATH?:'/','secure'=>is_ssl(),'httponly'=>true,'samesite'=>'Lax'));
    tdbs_agenda_back();
}
function tdbs_agenda_logout() {
    if (!wp_verify_nonce((string)($_POST['_wpnonce']??''),'tdbs_agenda_out')) tdbs_agenda_back('expired');
    $token=(string)($_COOKIE[TDBS_AGENDA_COOKIE]??'');
    if ($token!=='') delete_transient('tdbs_ags_'.hash('sha256',$token));
    setcookie(TDBS_AGENDA_COOKIE,'',array('expires'=>time()-3600,'path'=>COOKIEPATH?:'/','secure'=>is_ssl(),'httponly'=>true,'samesite'=>'Lax'));
    tdbs_agenda_back('out');
}
foreach (array('admin_post_tdbs_agenda_login','admin_post_nopriv_tdbs_agenda_login') as $hook) add_action($hook,'tdbs_agenda_login');
foreach (array('admin_post_tdbs_agenda_logout','admin_post_nopriv_tdbs_agenda_logout') as $hook) add_action($hook,'tdbs_agenda_logout');
/** The page of the agenda: never in a page cache nor in search engines. */
add_action('template_redirect',function(){
    $post=get_queried_object();
    if (!($post instanceof WP_Post) || !has_shortcode((string)$post->post_content,'tdbs_agenda')) return;
    if (!defined('DONOTCACHEPAGE')) define('DONOTCACHEPAGE',true);
    nocache_headers();header('Cache-Control: no-store, private');header('X-Robots-Tag: noindex, nofollow');
});
add_filter('wp_robots',function($robots){
    $post=get_queried_object();
    return $post instanceof WP_Post && has_shortcode((string)$post->post_content,'tdbs_agenda') ? array_merge($robots,array('noindex'=>true,'nofollow'=>true)) : $robots;
});
/** The site's requests not yet taken by the PC, as the agenda shows them: "in attesa di conferma"; 2.19.0 the agenda's own "da sincronizzare". */
function tdbs_agenda_pending() {
    global $wpdb;$out=array();$snap=tdbs_pc_snapshot();
    foreach ((array)$wpdb->get_results('SELECT slot,data FROM '.tdbs_pc_table().' ORDER BY created',ARRAY_A) as $row) {
        $slot=json_decode($row['slot'],true);$client=tdbs_pc_open($row['data']);
        if (!is_array($slot) || !is_array($client)) continue;
        $sv=tdbs_pc_service($snap,(string)($slot['service']??''));$name=$sv?(string)$sv['name']:'Richiesta dal sito';$who='';
        foreach ((array)($sv['options']??array()) as $o) if (($o['id']??'')===($slot['option']??'')) $name.=' · '.$o['name'];
        foreach ((array)($sv['resources']??array()) as $r) if (($r['id']??'')===($slot['resource']??'')) $who=(string)$r['name'];
        $out[]=array('start'=>$slot['date'].'T'.$slot['time'],'end'=>$slot['date'].'T'.$slot['end'],'service'=>$name,'resource'=>(string)$slot['resource'],'operator'=>$who,
            'status'=>($slot['source']??'')==='agenda'?'Da sincronizzare'.(!empty($slot['free'])?' · altro orario, lo controlla il PC':''):'In attesa di conferma · dal sito','pending'=>true,'client'=>trim($client['first_name'].' '.$client['last_name']),'phone'=>$client['phone'],'notes'=>$client['notes']);
    }
    return $out;
}
/** 2.19.0: the day or the week in one table, the operators' row and the bookings in the same horizontal scroll, also on
    the phone: the operators stay on top, the days of the week on the left. */
function tdbs_agenda_table($a,$days) {
    $cols=array();foreach ((array)$a['operators'] as $o) $cols[(string)$o['id']]=(string)$o['name'];
    $cells=array();
    foreach ((array)$a['bookings'] as $b) {
        $d=substr((string)$b['start'],0,10);
        if (in_array($d,$days,true)) $cells[$d][isset($cols[(string)$b['resource']])?(string)$b['resource']:'-'][]=$b;
    }
    foreach ($cells as $x) if (isset($x['-'])) {$cols['-']='Senza operatore';break;}
    if (!$cols) $cols['-']='Prenotazioni';
    $week=count($days)>1;$name=function($d,$f){return wp_date($f,strtotime($d.' 12:00 UTC'),new DateTimeZone('UTC'));};
    $html=($week?'':'<h3>'.esc_html($name($days[0],'l j F')).'</h3>').'<div class="tdbs-agenda-scroll" role="region" aria-label="Prenotazioni" tabindex="0"><table class="tdbs-agenda-table'.($week?' is-week':'').'"><thead><tr>'
        .($week?'<th scope="col" class="tdbs-agenda-corner">Giorno</th>':'');
    foreach ($cols as $c) $html.='<th scope="col">'.esc_html($c).'</th>';
    $html.='</tr></thead><tbody>';
    foreach ($days as $d) {
        $html.='<tr>'.($week?'<th scope="row">'.esc_html($name($d,'D j M')).'</th>':'');
        foreach (array_keys($cols) as $k) {
            $items=$cells[$d][$k]??array();
            usort($items,function($x,$y){return strcmp($x['start'],$y['start']);});
            $html.='<td>'.($items?'':($week?'<span class="tdbs-agenda-empty" aria-hidden="true">—</span><span class="screen-reader-text">Nessuna prenotazione</span>':'<p class="tdbs-agenda-empty">Nessuna prenotazione</p>'));
            foreach ($items as $b) {
                $when=substr($b['start'],11,5).'–'.substr($b['end'],11,5);$dl='';
                foreach (array('Quando'=>$when,'Servizio'=>$b['service'],'Con'=>$b['operator'],'Cliente'=>$b['client'],'Telefono'=>$b['phone'],'Note'=>$b['notes'],'Stato'=>$b['status']) as $key=>$v) if ((string)$v!=='')
                    $dl.='<dt>'.$key.'</dt><dd>'.($key==='Telefono'?'<a href="tel:'.esc_attr(preg_replace('/[^0-9+]/','',(string)$v)).'">'.esc_html($v).'</a>':nl2br(esc_html($v))).'</dd>';
                $html.='<details class="tdbs-agenda-booking'.(!empty($b['pending'])?' is-pending':'').'"><summary><strong>'.esc_html($when).'</strong> '.esc_html($b['service'].($b['client']?' · '.$b['client']:''))
                    .(!empty($b['pending'])?' <span class="tdbs-agenda-chip">'.esc_html($b['status']).'</span>':'').'</summary><dl>'.$dl.'</dl></details>';
            }
            $html.='</td>';
        }
        $html.='</tr>';
    }
    return $html.'</tbody></table></div>';
}
/** 2.19.0: how long the service takes with that operator, from a free time the PC sent (of another operator if none), else 30 minutes. */
function tdbs_pc_length($sv,$option,$rid) {
    $all=(array)($sv['slots'][$option]??array());
    foreach (array_merge(array((array)($all[$rid]??array())),array_values($all)) as $days) foreach ((array)$days as $ranges) foreach ((array)$ranges as $range)
        if (preg_match('/^(\d{2}):(\d{2})-(\d{2}):(\d{2})$/',(string)$range,$m) && ($n=($m[3]*60+$m[4])-($m[1]*60+$m[2]))>0) return $n;
    return 30;
}
/** 2.19.0 Nuovo appuntamento, from the agenda with its password: a free time of the PC's data, or with Altro orario any
    time (the PC checks it); the client's data sealed in the same table of the visitors' requests, with source "agenda".
    Its time is no longer free on the site at once; no email from here, the PC sends the confirmation. */
function tdbs_agenda_book(WP_REST_Request $request) {
    global $wpdb;
    $p=$request->get_json_params();
    if (!is_array($p)) return tdbs_pc_error('Richiesta non valida.');
    if (!wp_verify_nonce((string)($p['nonce']??''),'tdbs_agenda_book')) return tdbs_pc_error('Pagina scaduta: ricaricala e riprova.',403);
    if (!tdbs_agenda_session()) return tdbs_pc_error('Agenda chiusa: ricarica la pagina e scrivi la password.',403);
    $snap=tdbs_pc_on()?tdbs_pc_fresh():null;
    if (!$snap) return tdbs_pc_error('Il sito non ha dati recenti dal PC: accendilo e riprova.',404);
    if (!tdbs_pc_throttle('tdbs_rla',30,120)) return tdbs_pc_error('Troppi appuntamenti in poco tempo: riprova tra un’ora.',429);
    $sv=tdbs_pc_service($snap,(string)($p['service']??''));
    if (!$sv) return tdbs_pc_error('Servizio non disponibile.');
    $option=(string)($p['option']??'');$options=array_column((array)($sv['options']??array()),'id');
    if ($options ? !in_array($option,$options,true) : $option!=='') return tdbs_pc_error('Scegli la prestazione.');
    $resource=(string)($p['resource']??'');$any=$resource==='';$other=!empty($p['other']);
    if (!$any && !in_array($resource,array_column((array)($sv['resources']??array()),'id'),true)) return tdbs_pc_error('Scegli l’operatore.');
    if ($other && $any) return tdbs_pc_error('Con «Altro orario» scegli l’operatore.');
    $date=(string)($p['date']??'');$time=(string)($p['time']??'');
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/',$date) || !preg_match('/^([01]\d|2[0-3]):[0-5]\d$/',$time)) return tdbs_pc_error('Scegli giorno e orario.');
    try {$tz=new DateTimeZone((string)($snap['timezone']??'Europe/Rome'));} catch (Exception $e) {$tz=wp_timezone();}
    $now=new DateTime('now',$tz);
    if ($date.' '.$time<=$now->format('Y-m-d H:i')) return tdbs_pc_error('Questo orario è già passato.');
    $client=array();
    foreach (array('first_name','last_name','phone') as $k) {
        $client[$k]=sanitize_text_field((string)($p[$k]??''));
        if ($client[$k]==='' || strlen($client[$k])>100) return tdbs_pc_error('Scrivi nome, cognome e telefono del cliente.');
    }
    if (!preg_match('/^[0-9 +().\/-]{6,30}$/',$client['phone'])) return tdbs_pc_error('Numero di telefono non valido, per esempio 333 123 4567.');
    foreach (array('first_name','last_name') as $k) $client[$k]=tdbs_name_case($client[$k]);
    $client['email']=strtolower(trim((string)($p['email']??'')));
    if ($client['email']!=='' && (!is_email($client['email']) || strlen($client['email'])>254)) return tdbs_pc_error('Email non valida.');
    $client['notes']=mb_substr(sanitize_textarea_field((string)($p['notes']??'')),0,4000);
    $client['notices']=true;$client['marketing']=false;
    $wpdb->get_var("SELECT GET_LOCK('tdbs_book',10)");
    if ($other) {$m=min(1439,(int)substr($time,0,2)*60+(int)substr($time,3,2)+tdbs_pc_length($sv,$option,$resource));$hit=array($time.'-'.sprintf('%02d:%02d',intdiv($m,60),$m%60),$resource);}
    else {
        $hit=null;
        foreach ((array)(tdbs_pc_free($snap,$sv,$option,$resource)[$date]??array()) as $x) if (substr($x[0],0,5)===$time) {$hit=$x;break;}
        if (!$hit) {$wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");return tdbs_pc_error('Questo orario non è più libero: scegline un altro o usa «Altro orario».',409,array('reload'=>true));}
    }
    $id=bin2hex(random_bytes(16));
    $saved=$wpdb->insert(tdbs_pc_table(),array('id'=>$id,'created'=>current_time('mysql',true),'slot'=>wp_json_encode(array('service'=>$sv['id'],'option'=>$option,'resource'=>$hit[1],'any'=>$any,'date'=>$date,'time'=>$time,'end'=>substr($hit[0],6,5),'source'=>'agenda','free'=>$other)),'data'=>tdbs_pc_seal($client)));
    $wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");
    if (!$saved) return tdbs_pc_error('Non è stato possibile salvare l’appuntamento: riprova.',500);
    if (tdbs_master()) {
        // 2.21.0: on a free time the client gets the confirmation now; on «Altro orario» when the PC confirms it
        $info=tdbs_site_info($snap,$sv,$option,$any?'':$hit[1],$date,$time,substr($hit[0],6,5),$client);
        tdbs_state_put('wp-'.$id,$other?'pending':'confirmed',$info['start'],$info,array(),time());
        if (!$other) tdbs_tell('confirmed','wp-'.$id,$info);
    }
    $response=new WP_REST_Response(array('ok'=>true),201);
    $response->header('Cache-Control','no-store');
    return $response;
}
/** 2.19.0: the Nuovo appuntamento form of the agenda, from the PC's data (agenda.js): the services of the booking form. */
function tdbs_agenda_form() {
    $snap=tdbs_pc_fresh();
    if (!$snap) return '<p class="tdbs-agenda-status">Per aggiungere appuntamenti servono dati recenti dal PC: accendilo e attendi la sincronizzazione.</p>';
    $services=array();
    foreach ((array)$snap['services'] as $sv) if (is_array($sv)) $services[]=array('id'=>(string)$sv['id'],'name'=>(string)$sv['name'],
        'options'=>array_map(function($o){return array('id'=>(string)$o['id'],'name'=>(string)$o['name']);},(array)($sv['options']??array())),
        'resources'=>array_map(function($r){return array('id'=>(string)$r['id'],'name'=>(string)$r['name']);},(array)($sv['resources']??array())));
    if (!$services) return '';
    try {$tz=new DateTimeZone((string)($snap['timezone']??'Europe/Rome'));} catch (Exception $e) {$tz=wp_timezone();}
    $today=(new DateTime('now',$tz))->format('Y-m-d');
    wp_enqueue_script('tdbs-agenda',plugins_url('agenda.js',__FILE__),array(),TDBS_VERSION,true);
    $config=array('rest'=>esc_url_raw(rest_url('tdbs/v1/')),'nonce'=>wp_create_nonce('tdbs_agenda_book'),'wp'=>wp_create_nonce('wp_rest'),'services'=>$services);
    $field=function($name,$label,$type='text',$required=true){return '<p><label>'.esc_html($label).'<br><input name="'.$name.'" type="'.$type.'"'.($required?' required':'').' maxlength="'.($type==='email'?254:100).'" autocomplete="off"></label></p>';};
    return '<details class="tdbs-agenda-new"><summary class="button">Nuovo appuntamento</summary><form class="tdbs-agenda-form" data-config="'.esc_attr(wp_json_encode($config)).'" novalidate>'
        .'<p><label>Servizio<br><select name="service" required></select></label></p>'
        .'<p class="tdbs-agenda-option" hidden><label>Prestazione<br><select name="option"></select></label></p>'
        .'<p><label>Operatore<br><select name="resource"></select></label></p>'
        .'<p><label>Giorno<br><input type="date" name="date" required min="'.esc_attr($today).'" value="'.esc_attr($today).'"></label></p>'
        .'<div class="tdbs-pc-slots" role="group" aria-label="Orari liberi"></div>'
        .'<p><label>Altro orario<br><input type="time" name="other" step="300"></label><br><small>Solo se serve un orario che non è tra quelli liberi. <strong>Lo controlla il PC</strong>: se è libero diventa una prenotazione confermata, altrimenti va tra le <strong>Richieste da confermare</strong>.</small></p>'
        .'<p class="tdbs-pc-status" role="status" aria-live="polite"></p>'
        .'<fieldset><legend>Cliente</legend>'.$field('first_name','Nome').$field('last_name','Cognome').$field('phone','Telefono','tel').$field('email','Email (facoltativa)','email',false).'</fieldset>'
        .'<p><label>Note<br><textarea name="notes" rows="3" maxlength="4000"></textarea></label></p>'
        .'<p><button type="submit">Salva l’appuntamento</button></p></form></details>';
}
function tdbs_agenda_shortcode() {
    wp_enqueue_style('tdbs-widget',plugins_url('widget.css',__FILE__),array(),TDBS_VERSION);
    $p=tdbs_pc();$post=esc_url(admin_url('admin-post.php'));$back=esc_attr(remove_query_arg(array('tdbs_agenda')));
    if (empty($p['agenda'])) return '<p class="tdbs-pc-off">Agenda non attiva.</p>';
    if (!tdbs_agenda_session()) {
        $msg=array('wrong'=>'Password non corretta.','locked'=>'Troppi tentativi: riprova tra 15 minuti.','expired'=>'Pagina scaduta: riprova.','out'=>'Agenda chiusa.')[is_string($_GET['tdbs_agenda']??null)?$_GET['tdbs_agenda']:'']??'';
        return '<div class="tdbs-agenda"><form class="tdbs-agenda-login" method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_agenda_login"><input type="hidden" name="back" value="'.$back.'">'.wp_nonce_field('tdbs_agenda','_wpnonce',false,false)
            .'<p><label>Password dell’agenda<br><input type="password" name="password" required autocomplete="current-password"></label></p><p><button type="submit">Apri l’agenda</button></p>'
            .'<p class="tdbs-agenda-status" role="status">'.esc_html($msg).'</p></form></div>';
    }
    $stored=get_option('tdbs_agenda',array());$a=is_array($stored)&&!empty($stored['sealed'])?tdbs_pc_open($stored['sealed']):null;
    if (!is_array($a) && tdbs_archive_on()) $a=array('timezone'=>(string)(tdbs_pc_snapshot()['timezone']??'Europe/Rome'),'operators'=>array(),'bookings'=>array());
    $out='<form class="inline" method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_agenda_logout"><input type="hidden" name="back" value="'.$back.'">'.wp_nonce_field('tdbs_agenda_out','_wpnonce',false,false).'<button type="submit">Esci</button></form>';
    if (!is_array($a)) return '<div class="tdbs-agenda"><p>L’agenda arriva con la prossima sincronizzazione del PC.</p>'.$out.'</div>';
    try {$tz=new DateTimeZone((string)($a['timezone']??'Europe/Rome'));} catch (Exception $e) {$tz=wp_timezone();}
    $today=(new DateTime('now',$tz))->format('Y-m-d');$week=($_GET['vista']??'')==='settimana';
    $day=preg_match('/^\d{4}-\d{2}-\d{2}$/',(string)($_GET['giorno']??''))?(string)$_GET['giorno']:$today;
    $shift=function($d,$n){return gmdate('Y-m-d',strtotime($d.' 12:00 UTC')+$n*DAY_IN_SECONDS);};
    // 2.18.0: with the archive, the days chosen from all of it, also the past ones
    if (tdbs_archive_on()) {$first=$week?$shift($day,-((int)gmdate('N',strtotime($day.' 12:00 UTC'))-1)):$day;$a['bookings']=tdbs_archive_bookings($first,$week?$shift($first,6):$day,$a);}
    $a['bookings']=array_merge((array)($a['bookings']??array()),tdbs_agenda_pending());
    $link=function($label,$args,$on=null){return '<a class="button" href="'.esc_url(add_query_arg($args,remove_query_arg('nuovo'))).'"'.($on===null?'':' aria-current="'.($on?'true':'false').'"').'>'.$label.'</a>';};
    $step=$week?7:1;$v=$week?'settimana':'giorno';
    $bar='<nav class="tdbs-agenda-bar" aria-label="Agenda">'.$link('Giorno',array('vista'=>'giorno','giorno'=>$day),!$week).$link('Settimana',array('vista'=>'settimana','giorno'=>$day),$week)
        .$link('<span aria-hidden="true">‹</span><span class="screen-reader-text">Indietro</span>',array('vista'=>$v,'giorno'=>$shift($day,-$step))).$link('Oggi',array('vista'=>$v,'giorno'=>$today))
        .$link('<span aria-hidden="true">›</span><span class="screen-reader-text">Avanti</span>',array('vista'=>$v,'giorno'=>$shift($day,$step))).$out.'</nav>';
    $days=array($day);
    if ($week) {$first=$shift($day,-((int)gmdate('N',strtotime($day.' 12:00 UTC'))-1));$days=array();for ($i=0;$i<7;$i++) $days[]=$shift($first,$i);}
    $at=(int)($stored['received']??0);
    // 2.19.0: Nuovo appuntamento; once saved, the page opens again on its day with this line
    $saved=($_GET['nuovo']??'')==='1'?'<p class="tdbs-agenda-status" role="status">Appuntamento salvato: qui sotto è «Da sincronizzare» finché il PC non lo prende, entro 2 minuti con il PC acceso.</p>':'';
    return '<div class="tdbs-agenda"><p class="tdbs-agenda-updated">Aggiornata alle '.esc_html(wp_date('H:i',$at)).' del '.esc_html(wp_date('d/m',$at)).'</p>'.$saved.tdbs_agenda_form().$bar.'<div class="tdbs-agenda-body">'.tdbs_agenda_table($a,$days).'</div></div>';
}
add_action('init',function(){add_shortcode('tdbs_agenda','tdbs_agenda_shortcode');});

/* the settings page: Modalità PC (sincronizzazione) */
function tdbs_pc_settings($post) {
    $p=tdbs_pc();$snap=tdbs_pc_snapshot();
    global $wpdb;$waiting=!empty($p['secret'])?(int)$wpdb->get_var('SELECT COUNT(*) FROM '.tdbs_pc_table()):0;
    echo '<div class="card" style="max-width:860px"><h2>Modalità PC (sincronizzazione)</h2><p>Per il programma installato su un PC dell’attività, spesso spento e non raggiungibile da Internet. Quando il PC è acceso, ogni 2 minuti manda a questo sito servizi, operatori e orari liberi e prende le richieste arrivate: il modulo funziona anche a PC spento.</p>'
        .'<ol><li>Nel programma apri <em>Configura l’attività › Sito WordPress · modalità PC</em> (la vede chi amministra il programma). Scrivi l’indirizzo di questo sito, <code>'.esc_html(tdbs_site_origin()).'</code>, premi <em>Salva</em>, poi <em>Genera la chiave</em> e <em>Copia</em>.</li>'
        .'<li>Qui sotto incolla la chiave in <em>Chiave dal programma</em>, spunta <em>Usa la modalità PC</em> e premi <em>Salva la modalità PC</em>.</li>'
        .'<li>Metti lo shortcode <code>[td_booking_suite]</code> in una pagina, per esempio Prenota.</li>'
        .'<li>Nel programma accendi <em>Sincronizzazione attiva</em>, premi <em>Salva</em> e poi <em>Sincronizza ora</em>. La riga in alto nel programma dice com’è andata; qui compare «Ultimi dati dal PC».</li>'
        .'<li>Se il server tiene le pagine in una sua cache (per esempio la FastCGI cache di aaPanel), escludi la pagina con il modulo: altrimenti mostra orari vecchi.</li></ol>';
    if (empty($snap['received'])) echo '<p><strong>Ancora nessun dato dal PC.</strong> Arrivano alla prima sincronizzazione con la chiave giusta.</p>';
    else echo '<p>Ultimi dati dal PC: '.esc_html(wp_date('d/m/Y H:i',(int)$snap['received'])).', '.count((array)($snap['services']??array())).' servizi. Richieste in attesa del PC: '.$waiting.'.</p>';
    echo '<form method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_pc_save">';wp_nonce_field('tdbs_pc_save');
    echo '<table class="form-table"><tr><th>Modalità</th><td><label><input type="checkbox" name="on" value="1"'.checked(!empty($p['on']),true,false).'> Usa la modalità PC per <code>[td_booking_suite]</code> al posto del modulo dal portale</label></td></tr>';
    echo '<tr><th><label for="tdbs-secret">Chiave dal programma</label></th><td><input type="password" class="regular-text" id="tdbs-secret" name="secret" autocomplete="off" placeholder="'.(!empty($p['secret'])?'Chiave salvata: lascia vuoto per tenerla':'Incolla la chiave').'"></td></tr>';
    echo '<tr><th><label for="tdbs-age">Dati validi per</label></th><td><input type="number" min="1" max="60" id="tdbs-age" name="max_age" value="'.esc_attr((string)($p['max_age']??7)).'"> giorni <p class="description">Se il PC non si fa sentire da più tempo, il modulo mostra “contattaci” invece degli orari.</p></td></tr>';
    echo '<tr><th><label for="tdbs-agenda-pw">Password dell’agenda</label></th><td><input type="password" class="regular-text" id="tdbs-agenda-pw" name="agenda" autocomplete="new-password" minlength="10" placeholder="'.(!empty($p['agenda'])?'Password salvata: lascia vuoto per tenerla':'Almeno 10 caratteri').'">'
        .(!empty($p['agenda'])?' <label><input type="checkbox" name="agenda_off" value="1"> Spegni l’agenda</label>':'')
        .'<p class="description"><strong>Agenda riservata</strong>: il titolare e i collaboratori vedono le prenotazioni dei prossimi giorni anche a PC spento, per esempio dal telefono, e aggiungono appuntamenti con <strong>Nuovo appuntamento</strong>: servizio, operatore, giorno, orario e cliente. Il PC li prende alla sincronizzazione dopo e manda al cliente la conferma, se ha l’email. Per spostare o annullare serve il programma. Senza password resta spenta.</p>'
        .'<ol class="description"><li>Scrivi qui una password, almeno 10 caratteri, e premi <em>Salva la modalità PC</em>. Una password nuova chiude le sessioni aperte.</li>'
        .'<li>Crea una pagina, per esempio Agenda, con lo shortcode <code>[tdbs_agenda]</code>. Non metterla nel menu del sito; Google non la mostra.</li>'
        .'<li>Escludi quella pagina dalla cache del server, per esempio dalla FastCGI cache di aaPanel. I plugin di cache di WordPress la saltano da soli, la cache del server no: senza esclusione le prenotazioni potrebbero arrivare a chi non ha la password.</li>'
        .'<li>Dai la password solo a chi deve vedere l’agenda. L’agenda arriva alla sincronizzazione dopo, entro 2 minuti con il PC acceso.</li></ol>'
        .'<p class="description">Il PC manda l’agenda cifrata con la chiave condivisa e il sito la decifra per mostrarla: chi amministra questo sito può tecnicamente leggerla.</p></td></tr>';
    // 2.18.0
    $st=!empty($p['secret'])?tdbs_archive_status():array('records'=>0,'size'=>0);$at=(int)get_option('tdbs_archive_at',0);
    echo '<tr><th>Archivio</th><td><label><input type="checkbox" name="archive" value="1"'.checked(!empty($p['archive']),true,false).(!empty($p['archive'])?' onchange="if(!this.checked&&!confirm(\'Spegnendo l\\u2019archivio, al salvataggio la copia nel sito viene cancellata. Per riaverla: riaccendilo e nel programma premi Carica tutto sul sito. Continuare?\'))this.checked=true"':'').'> Il sito come archivio centrale</label>'
        .'<p class="description">Il sito tiene una copia cifrata di prenotazioni, clienti con i dati fiscali, schede di lavoro, consensi e riepilogo degli scontrini, aggiornata dal PC a ogni sincronizzazione: un PC nuovo riprende tutto da qui con <em>Ripristina dal sito</em>. Di ogni record il sito tiene la versione modificata per ultima, qui o sul PC: se il sito è stato irraggiungibile, il PC manda le modifiche appena torna, e <em>Sincronizza con il sito</em> nel programma allinea le due parti. Va acceso anche nel programma, nella card <em>Sito WordPress · modalità PC</em>. Con l’archivio, l’agenda riservata mostra anche i giorni passati. Spegnendolo, l’archivio nel sito viene cancellato: per riaverlo, riaccendilo e nel programma premi <em>Carica tutto sul sito</em>.</p>'
        .'<p>'.($at?'Record: '.(int)$st['records'].', spazio: '.esc_html(size_format((int)$st['size'])).', ultimo invio dal PC: '.esc_html(wp_date('d/m/Y H:i',$at)):'Ancora nessun invio dal PC.').'</p></td></tr></table>';
    submit_button('Salva la modalità PC','secondary');echo '</form>';
    echo '<h3>Per il firewall del sito</h3><p>Il PC chiama solo questi indirizzi, sempre in <strong>POST</strong> con dati JSON, con le intestazioni <code>X-TDBS-Time</code> e <code>X-TDBS-Signature</code> e corpo fino a <strong>512 KB</strong> per l’archivio (gli orari liberi di <code>/sync</code> di solito molto meno; con tanti servizi e 90 giorni possono superare 1 MB: se il proxy risponde 413, alza il limite del corpo a 5 MB):</p><ul style="list-style:disc;padding-left:20px"><li><code>/wp-json/tdbs/v1/sync</code></li><li><code>/wp-json/tdbs/v1/archive/push</code> e <code>/wp-json/tdbs/v1/archive/pull</code>, sotto <code>/wp-json/tdbs/v1/archive/</code></li></ul>'
        .'<p>Nel WAF: <em>whitelist › tipo «path»</em>, aggiungi due voci con nota «TD Booking Suite – PC»: <code>/wp-json/tdbs/v1/sync</code> e <code>/wp-json/tdbs/v1/archive/</code>. Per limitarle a questo sito: <code>'.esc_html((string)wp_parse_url(home_url(),PHP_URL_HOST)).'|/wp-json/tdbs/v1/sync</code> (host esatto: il www vuole una voce sua). Non mettere in whitelist tutto <code>/wp-json/tdbs/v1/</code>: <code>/slots</code> (GET), <code>/book</code> (POST) e <code>/agenda/book</code> (POST, il <strong>Nuovo appuntamento</strong> dell’agenda) li usa il browser di chi apre il sito e devono restare sotto il firewall.</p></div>';
}
add_action('admin_post_tdbs_pc_save',function(){
    global $wpdb;
    tdbs_admin_check('tdbs_pc_save');
    $old=tdbs_pc();$secret=trim((string)wp_unslash($_POST['secret']??''));
    $new=array('on'=>!empty($_POST['on']),'archive'=>!empty($_POST['archive']),'secret'=>(string)($old['secret']??''),'max_age'=>max(1,min(60,absint($_POST['max_age']??7))),'agenda'=>(string)($old['agenda']??''),'agenda_ver'=>(string)($old['agenda_ver']??''));
    // 2.17.1: the agenda's password, hashed; a new one (or the agenda off) closes the sessions opened before
    $password=(string)wp_unslash($_POST['agenda']??'');
    if (!empty($_POST['agenda_off'])) {$new['agenda']='';$new['agenda_ver']='';delete_option('tdbs_agenda');}
    elseif ($password!=='') {
        if (strlen($password)<10) {tdbs_flash('La password dell’agenda deve avere almeno 10 caratteri.');tdbs_back();}
        $new['agenda']=wp_hash_password($password);$new['agenda_ver']=bin2hex(random_bytes(8));
    }
    if ($secret!=='') {
        if (!preg_match('/^[A-Za-z0-9_-]{32,100}$/',$secret)) {tdbs_flash('La chiave non è valida: copiala di nuovo dal programma.');tdbs_back();}
        if ($secret!==$new['secret'] && $new['secret']!=='') {
            // the requests not yet taken by the PC stay readable with the new key
            $table=tdbs_pc_table();
            foreach ((array)$wpdb->get_results("SELECT id,data FROM $table",ARRAY_A) as $row) {
                $client=tdbs_pc_open($row['data'],$new['secret']);
                if (is_array($client)) $wpdb->update($table,array('data'=>tdbs_pc_seal($client,$secret)),array('id'=>$row['id']));
            }
            // 2.18.0: and the archive, for the new PC that brought the new key; page by page, it can be large
            $at=tdbs_archive_table();$last=array('','');
            do {
                $rows=(array)$wpdb->get_results($wpdb->prepare("SELECT kind,ref,data FROM $at WHERE (kind,ref)>(%s,%s) ORDER BY kind,ref LIMIT 500",$last[0],$last[1]),ARRAY_A);
                foreach ($rows as $row) {
                    $last=array($row['kind'],$row['ref']);$rec=$row['data']!==''?tdbs_pc_open($row['data'],$new['secret']):null;
                    if (is_array($rec)) $wpdb->update($at,array('data'=>tdbs_pc_seal($rec,$secret)),array('kind'=>$row['kind'],'ref'=>$row['ref']));
                }
            } while (count($rows)===500);
        }
        if ($secret!==$new['secret']) delete_option('tdbs_agenda');  // sealed with the old key: the PC sends it again
        $new['secret']=$secret;
    }
    if ($new['on'] && $new['secret']==='') {$new['on']=false;tdbs_flash('Incolla prima la chiave generata nel programma.');update_option('tdbs_pc',$new,false);tdbs_back();}
    // the archive off here: deleted, it holds personal data the PC no longer updates nor erases
    if (empty($new['archive']) && !empty($old['archive'])) {$wpdb->query('DELETE FROM '.tdbs_archive_table());delete_option('tdbs_archive_at');}
    update_option('tdbs_pc',$new,false);
    tdbs_flash($new['on']?'Modalità PC attiva: il modulo usa i dati che manda il PC.':'Modalità PC salvata, spenta.','success');tdbs_back();
});

/** 2.21.0: a name typed all in small or all in capital letters, as names are written: «mario rossi», «D'AMICO» → «Mario
    Rossi», «D'Amico»; one with capitals of its own stays as written («Maria DeLuca»). */
function tdbs_name_case($name) {
    if ($name!==mb_strtolower($name) && $name!==mb_strtoupper($name)) return $name;
    return preg_replace_callback("/(^|[\\s'’-])(\\p{L})/u",function($m){return $m[1].mb_strtoupper($m[2]);},mb_strtolower($name));
}
/** 2.21.0: what an email needs of a booking made on the site. */
function tdbs_site_info($snap,$sv,$option,$rid,$date,$time,$end,$client) {
    $tz=tdbs_tz();$name=(string)$sv['name'];$who='';
    foreach ((array)($sv['options']??array()) as $o) if (($o['id']??'')===$option) $name.=' · '.$o['name'];
    foreach ((array)($sv['resources']??array()) as $r) if ($rid!=='' && ($r['id']??'')===$rid) $who=(string)$r['name'];
    return array('first_name'=>$client['first_name'],'last_name'=>$client['last_name'],'email'=>$client['email'],'phone'=>$client['phone'],'notices'=>!empty($client['notices']),
        'service'=>$name,'service_id'=>(string)$sv['id'],'operator'=>$who,'location'=>(string)($sv['location']??''),
        'start'=>(new DateTime($date.' '.$time,$tz))->getTimestamp(),'end'=>(new DateTime($date.' '.$end,$tz))->getTimestamp());
}
require_once __DIR__.'/mails.php';
