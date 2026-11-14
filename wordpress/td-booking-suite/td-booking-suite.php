<?php
/**
 * Plugin Name: TD Booking Suite — Prenotazioni
 * Description: Mostra nel sito il modulo di prenotazione di TD Booking Suite. Due modi: collegato al portale con un codice, e si configura da solo; oppure in modalità PC, con il programma su un PC spesso spento: il modulo usa i dati che il PC manda e le richieste restano cifrate nel sito solo finché il PC non le prende. Con una password, [tdbs_agenda] mostra al titolare e ai collaboratori le prenotazioni mandate cifrate dal PC e può aggiungere appuntamenti, che il PC prende alla sincronizzazione. Il sito può fare da archivio centrale cifrato del programma, da cui un PC nuovo riprende tutto: di ogni record tiene la versione modificata per ultima. Può anche gestire prenotazioni ed email: conferma subito chi prenota dal sito e manda conferme, promemoria, modifiche e annullamenti anche con il PC spento.
 * Version: 2.22.19
 * Requires at least: 6.2
 * Requires PHP: 8.0
 * Author: Tastiere Digitali
 * License: GPL-2.0-or-later
 */
defined('ABSPATH') || exit;

const TDBS_VERSION='2.22.19';

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
    tdbs_flash('Sito scollegato. Per togliere anche l’autorizzazione, scollegalo dal portale in Impostazioni e sito › Sito WordPress.','success');tdbs_back();
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
    echo '<div class="card" style="max-width:860px"><h2>'.($connected?'Collega a un’altra attività':'Collegati al portale').'</h2><ol><li>Nel portale apri <em>Impostazioni e sito › Sito WordPress</em> e premi <em>Genera il codice di collegamento</em>: vale 24 ore e una volta sola.</li><li>Qui sotto scrivi l’indirizzo del portale, quello che apri nel browser per entrare, e incolla il codice.</li><li>Premi <em>'.($connected?'Collega':'Collegati').'</em>: il plugin autorizza questo sito, legge sedi e servizi e, se lasci la spunta, crea la pagina Prenota con il modulo.</li><li>Per una sola sede o un solo servizio usa gli shortcode che compaiono qui dopo il collegamento.</li></ol>';
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
    tdbs_faces_settings($post);
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
        if (!in_array($kind,array('booking','contact','treatment','consent','sale','wait'),true) || !preg_match('/^[a-f0-9-]{36}$/',$ref) || !is_int($seq) || $seq<1 || !is_int($v) || $v<0 || ($day!=='' && !preg_match('/^\d{4}-\d{2}-\d{2}$/',$day)) || ($sealed!=='' && !preg_match('#^[A-Za-z0-9+/=]+$#',$sealed))) continue;
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
    $kind=in_array($data['kind']??'',array('booking','contact','treatment','consent','sale','wait'),true)?$data['kind']:'contact';
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
    foreach ((array)$wpdb->get_col('SELECT slot FROM '.tdbs_pc_table()) as $slot) {
        $slot=json_decode($slot,true);if (!is_array($slot)) continue;
        // 2.22.0: a visit of several services keeps each of its pieces busy, with the end of the first one its own
        $pieces=(array)($slot['extra']??array());$first=$slot;$first['end']=$pieces?(string)($slot['first_end']??$slot['end']):(string)$slot['end'];$out[]=$first;
        foreach ($pieces as $x) if (is_array($x)) $out[]=array('resource'=>(string)($x['resource']??''),'date'=>(string)$slot['date'],'time'=>(string)($x['time']??''),'end'=>(string)($x['end']??''));
    }
    return $out;
}
/** 2.22.0: more services in one visit, one after the other: the free times of the first (as tdbs_pc_free) that leave room
    for each next service (no variant), with the same operator or, when the location allows it, with another, waiting at
    most the location's minutes between two. Returns day => array(array(range, resource, pieces)), pieces one an extra:
    array('service','resource','time','end'). $extra: the ids of the services after the first. */
function tdbs_pc_free_visit($snap,$sv,$option,$resource,$extra) {
    if (!$extra) return tdbs_pc_free($snap,$sv,$option,$resource);
    $others=array();
    foreach ($extra as $id) {$x=tdbs_pc_service($snap,(string)$id);if (!$x || ($x['location_id']??'')!==($sv['location_id']??'')) return array();$others[]=$x;}
    $split=(string)($sv['split']??'need');$wait=(int)($sv['wait']??15);$out=array();
    $mins=function($t){return (int)substr($t,0,2)*60+(int)substr($t,3,2);};
    // the free times of each operator, once: of the first service (the chosen one, or every one of its operators) and of
    // each extra (every operator of it, no variant); tdbs_pc_free with no operator keeps one a time, which would hide the others
    $firsts=array();
    foreach ($resource!==''?array($resource):array_column((array)($sv['resources']??array()),'id') as $rid) $firsts[$rid]=tdbs_pc_free($snap,$sv,$option,$rid);
    $ofree=array();
    foreach ($others as $o) foreach (array_column((array)($o['resources']??array()),'id') as $rid) $ofree[$o['id']][$rid]=tdbs_pc_free($snap,$o,'',$rid);
    $days=array();
    foreach ($firsts as $rid=>$free) foreach ($free as $day=>$times) foreach ($times as $x) $days[$day][substr($x[0],0,5)][]=array($x[0],$rid);
    ksort($days);
    foreach ($days as $day=>$starts) {
        ksort($starts);
        foreach ($starts as $candidates) foreach ($candidates as $x) {
            $first=$x[1];$pieces=array();$end=substr($x[0],6,5);$ok=true;
            foreach ($others as $o) {
                // the one who did the previous piece first, then the others when the location splits a visit
                $ids=array($first);
                if ($split!=='same') foreach (array_keys((array)($ofree[$o['id']]??array())) as $rid) if ($rid!==$first) $ids[]=$rid;
                $limit=$mins($end)+$wait;$found=null;
                foreach ($split==='need'?array(array($first),$ids):array($ids) as $try) {
                    $best=null;
                    foreach ($try as $rid) foreach ((array)($ofree[$o['id']][$rid][$day]??array()) as $y) {
                        $a=$mins(substr($y[0],0,5));
                        if ($a<$mins($end) || $a>$limit) continue;
                        if ($best===null || $a<$mins(substr($best[0],0,5))) $best=$y;  // strictly earlier: on a tie the first operator tried wins
                    }
                    if ($best) {$found=$best;break;}
                }
                if (!$found) {$ok=false;break;}
                $pieces[]=array('service'=>(string)$o['id'],'resource'=>$found[1],'time'=>substr($found[0],0,5),'end'=>substr($found[0],6,5));
                $first=$found[1];$end=substr($found[0],6,5);
            }
            if ($ok) {$out[$day][]=array($x[0],$x[1],$pieces);break;}  // one a start, as tdbs_pc_free does
        }
    }
    return $out;
}
function tdbs_pc_extra($snap,$sv,$value) {
    /** the ids asked as extras (array or "a,b"), at most 4, each a service of the snapshot of the same location, the first one excluded */
    $ids=array_values(array_unique(array_filter(array_map('strval',is_array($value)?$value:explode(',',(string)$value)))));
    if (count($ids)>4) return null;
    if (($sv['location_id']??'')==='') return $ids?null:array();  // a PC before 2.22.0: no visits from the site
    foreach ($ids as $id) {$x=tdbs_pc_service($snap,$id);if (!$x || $id===($sv['id']??'') || ($x['location_id']??'')!==($sv['location_id']??'')) return null;}
    return $ids;
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
    $extra=tdbs_pc_extra($snap,$sv,(string)$request->get_param('extra'));
    if ($extra===null) return tdbs_pc_error('Servizi da aggiungere non validi.');
    $response=new WP_REST_Response(array('ok'=>true,'days'=>(object)tdbs_pc_free_visit($snap,$sv,(string)$request->get_param('option'),(string)$request->get_param('resource'),$extra)),200);
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
    $extra=tdbs_pc_extra($snap,$sv,$p['extra']??array());
    if ($extra===null) {$wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");return tdbs_pc_error('Servizi da aggiungere non validi.');}
    $free=tdbs_pc_free_visit($snap,$sv,$option,$any?'':$resource,$extra);$hit=null;
    foreach ((array)($free[$date]??array()) as $x) if (substr($x[0],0,5)===$time && ($any || $x[1]===$resource)) {$hit=$x;break;}
    if (!$hit) {$wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");return tdbs_pc_error('Questo orario non è più libero: scegline un altro.',409,array('reload'=>true));}
    $id=bin2hex(random_bytes(16));$pieces=(array)($hit[2]??array());$last=$pieces?end($pieces)['end']:substr($hit[0],6,5);
    // 2.22.0: a visit: the end of the whole visit in end, the first service's own in first_end, each next service in extra
    $saved=$wpdb->insert(tdbs_pc_table(),array('id'=>$id,'created'=>current_time('mysql',true),'slot'=>wp_json_encode(array('service'=>$sv['id'],'option'=>$option,'resource'=>$hit[1],'any'=>$any,'date'=>$date,'time'=>$time,'end'=>$last,'first_end'=>substr($hit[0],6,5),'extra'=>$pieces)),'data'=>tdbs_pc_seal($client)));
    $wpdb->get_var("SELECT RELEASE_LOCK('tdbs_book')");
    if (!$saved) return tdbs_pc_error('Non è stato possibile salvare la richiesta: riprova.',500);
    $who='';foreach ((array)($sv['resources']??array()) as $r) if (!$any && ($r['id']??'')===$hit[1]) $who=' con '.$r['name'];
    if (tdbs_master()) {
        // 2.21.0: the site in charge confirms it now (or takes the request, for a service the business confirms) and writes to the client
        $pending=!empty($sv['approval']);$info=tdbs_site_info($snap,$sv,$option,$any?'':$hit[1],$date,$time,$last,$client,$pieces);
        tdbs_state_put('wp-'.$id,$pending?'pending':'confirmed',$info['start'],$info,array(),time());
        tdbs_tell($pending?'requested':'confirmed','wp-'.$id,$info);
        tdbs_notify($pending?'Nuova richiesta dal sito':'Nuova prenotazione dal sito',$info);
        return new WP_REST_Response(array('ok'=>true,'message'=>$pending?'Richiesta ricevuta: ti scriviamo appena la confermiamo.':'Prenotazione confermata: ti abbiamo mandato un’email con i dettagli.'),201);
    }
    $day=DateTime::createFromFormat('Y-m-d',$date);
    wp_mail($client['email'],'Richiesta ricevuta · '.sanitize_text_field((string)($snap['name']??'')),
        'Ciao '.$client['first_name'].",\n\nabbiamo ricevuto la tua richiesta: ".sanitize_text_field(tdbs_visit_name($snap,$sv,$pieces)).$who.' il '.($day?$day->format('d/m/Y'):$date).' alle '.$time.".\nRiceverai la conferma per email.\n\n".sanitize_text_field((string)($snap['name']??'')));
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
        $services[]=array('id'=>(string)$sv['id'],'name'=>(string)$sv['name'],'description'=>(string)($sv['description']??''),'picks'=>!empty($sv['picks']),'location_id'=>(string)($sv['location_id']??''),
            'options'=>array_map(function($o){return array('id'=>(string)$o['id'],'name'=>(string)$o['name']);},(array)($sv['options']??array())),
            'resources'=>array_map(function($r){return array('id'=>(string)$r['id'],'name'=>(string)$r['name']);},(array)($sv['resources']??array())));
    }
    if (!$services) return '<p class="tdbs-pc-off">Nessun servizio prenotabile online: contattaci.</p>';
    $t=(array)($snap['texts']??array());
    wp_enqueue_style('tdbs-widget',plugins_url('widget.css',__FILE__),array(),TDBS_VERSION);
    wp_enqueue_script('tdbs-pc',plugins_url('pc.js',__FILE__),array(),TDBS_VERSION,true);
    $config=array('rest'=>esc_url_raw(rest_url('tdbs/v1/')),'nonce'=>wp_create_nonce('tdbs_book'),'services'=>$services);
    // 2.22.0: the theme's own look: UIkit classes (YOOtheme Pro) on every field and button, harmless elsewhere; the time as a list
    $field=function($name,$label,$type='text',$auto=''){return '<div class="tdbs-pc-field"><label class="uk-form-label" for="tdbs-'.$name.'">'.esc_html($label).'</label><input id="tdbs-'.$name.'" class="uk-input" name="'.$name.'" type="'.$type.'" required maxlength="'.($type==='email'?254:100).'"'.($auto?' autocomplete="'.$auto.'"':'').'></div>';};
    $select=function($name,$label,$class='',$hidden=false){return '<div class="tdbs-pc-field'.($class?' '.$class:'').'"'.($hidden?' hidden':'').'><label class="uk-form-label" for="tdbs-'.$name.'">'.esc_html($label).'</label><select id="tdbs-'.$name.'" class="uk-select" name="'.$name.'"'.($class?'':' required').'></select></div>';};
    return '<div class="tdbs-pc uk-form-stacked" data-config="'.esc_attr(wp_json_encode($config)).'"><form class="tdbs-pc-form" novalidate>'
        .'<div class="tdbs-pc-grid">'.$select('service','Servizio').'<div class="tdbs-pc-field tdbs-pc-description-box" hidden><small class="tdbs-pc-description"></small></div>'
        .$select('option','Prestazione','tdbs-pc-option',true).$select('resource','Con chi','tdbs-pc-resource',true).'</div>'
        .'<div class="tdbs-pc-extra" hidden><div class="tdbs-pc-extra-list"></div><p class="tdbs-pc-extra-foot"><button type="button" class="tdbs-pc-add" hidden></button><small>Nella stessa visita, uno dopo l\'altro: scegli ancora dalla lista per aggiungerne</small></p></div>'
        .'<div class="tdbs-pc-grid">'.$select('date','Giorno').$select('time','Orario').'</div>'
        .'<p class="tdbs-pc-status" role="status" aria-live="polite"></p>'
        .'<fieldset class="uk-fieldset tdbs-pc-who"><legend class="uk-legend">I tuoi recapiti</legend><div class="tdbs-pc-grid">'.$field('first_name','Nome','text','given-name').$field('last_name','Cognome','text','family-name').$field('email','Email','email','email').$field('phone','Telefono','tel','tel').'</div></fieldset>'
        .'<div class="tdbs-pc-field"><label class="uk-form-label" for="tdbs-notes">Vuoi dirci qualcosa?</label><textarea id="tdbs-notes" class="uk-textarea" name="notes" rows="3" maxlength="4000"></textarea></div>'
        .'<details class="tdbs-pc-privacy"><summary>Come utilizziamo i tuoi dati</summary><p>'.nl2br(esc_html((string)($t['privacy']??''))).'</p></details>'
        .'<label class="tdbs-pc-check"><input class="uk-checkbox" type="checkbox" name="notices" checked> <span>'.esc_html((string)($t['notices']??'')).'</span></label>'
        .(!empty($t['consent'])?'<label class="tdbs-pc-check"><input class="uk-checkbox" type="checkbox" name="marketing"> <span>'.esc_html((string)$t['consent']).' (facoltativo)</span></label>':'')
        .'<p class="tdbs-pc-send"><button type="submit" class="uk-button uk-button-primary">Invia la richiesta</button></p>'
        .'<p class="tdbs-pc-updated">Disponibilità aggiornata alle '.esc_html(wp_date('H:i',(int)$snap['received'])).' del '.esc_html(wp_date('d/m',(int)$snap['received'])).'</p>'
        .'</form><div class="tdbs-pc-done uk-alert uk-alert-success" role="status" hidden></div></div>';
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
        if ($sv) $name=tdbs_visit_name($snap,$sv,(array)($slot['extra']??array())).(strpos($name,' · ')!==false?substr($name,strpos($name,' · ')):'');
        foreach ((array)($sv['resources']??array()) as $r) if (($r['id']??'')===($slot['resource']??'')) $who=(string)$r['name'];
        $out[]=array('start'=>$slot['date'].'T'.$slot['time'],'end'=>$slot['date'].'T'.$slot['end'],'service'=>$name,'resource'=>(string)$slot['resource'],'operator'=>$who,
            'status'=>($slot['source']??'')==='agenda'?'Da sincronizzare'.(!empty($slot['free'])?' · altro orario, lo controlla il PC':''):'In attesa di conferma · dal sito','pending'=>true,'client'=>trim($client['first_name'].' '.$client['last_name']),'phone'=>$client['phone'],'notes'=>$client['notes']);
    }
    return $out;
}
/** 2.22.1: the day or the week as a real agenda, as the program's: the hours on the left, a column per operator (the week:
    per day), each booking a block at its time with the operator's colour, the faces of the operators on top as a filter;
    a tap opens the detail (agenda.js). The hours come from the free times the PC sent for those days and from the
    bookings themselves, 8-20 when nothing is known. */
function tdbs_agenda_hours($snap,$days,$items) {
    $lo=null;$hi=null;$m=function($t){return (int)substr($t,0,2)*60+(int)substr($t,3,2);};
    foreach ((array)($snap['services']??array()) as $sv) foreach ((array)($sv['slots']??array()) as $byres) foreach ((array)$byres as $bydays) foreach ((array)$bydays as $d=>$ranges) if (in_array($d,$days,true))
        foreach ((array)$ranges as $r) if (preg_match('/^\d{2}:\d{2}-\d{2}:\d{2}$/',(string)$r)) {$a=$m(substr($r,0,5));$b=$m(substr($r,6,5));$lo=$lo===null?$a:min($lo,$a);$hi=$hi===null?$b:max($hi,$b);}
    if ($lo===null) {$lo=8*60;$hi=20*60;}
    foreach ($items as $x) {$lo=min($lo,$m(substr($x['start'],11,5)));$hi=max($hi,$m(substr($x['end'],11,5)));}
    return array((int)floor($lo/60)*60,(int)ceil(max($hi,$lo+60)/60)*60);
}
/* ---------- 2.22.1 the operators' faces: a photo chosen here (media library) per operator, else the one the program sends
   with the agenda (its Operatori photo, 128 px). tdbs_faces: operator id => attachment id. */
function tdbs_faces() {$f=get_option('tdbs_faces',array());return is_array($f)?$f:array();}
function tdbs_face($id,$fromPc='') {
    $f=tdbs_faces();
    if (!empty($f[$id]) && ($u=wp_get_attachment_image_url((int)$f[$id],'thumbnail'))) return $u;
    return (string)$fromPc;
}
function tdbs_agenda_people() {
    $stored=get_option('tdbs_agenda',array());$a=is_array($stored)&&!empty($stored['sealed'])?tdbs_pc_open($stored['sealed']):null;
    $out=array();
    foreach ((array)($a['operators']??array()) as $o) if (is_array($o) && ($o['id']??'')!=='') $out[(string)$o['id']]=array('name'=>(string)($o['name']??''),'photo'=>(string)($o['photo']??''));
    if (!$out) foreach ((array)(tdbs_pc_snapshot()['services']??array()) as $sv) foreach ((array)($sv['resources']??array()) as $r) if (($r['id']??'')!=='') $out[(string)$r['id']]=array('name'=>(string)($r['name']??''),'photo'=>'');
    return $out;
}
function tdbs_faces_settings($post) {
    $people=tdbs_agenda_people();
    if (!$people) return;
    wp_enqueue_media();$f=tdbs_faces();
    echo '<div class="card" style="max-width:860px"><h2>Foto degli operatori</h2><p class="description">Compaiono in cima alle colonne dell’agenda del sito e nell’app sul telefono. Scegli qui una foto dalla libreria media; senza, vale quella messa nel programma (Configura l’attività › Operatori), che arriva con la sincronizzazione.</p>';
    echo '<form method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_faces_save">';wp_nonce_field('tdbs_faces_save');
    echo '<table class="form-table">';
    foreach ($people as $id=>$o) {
        $mine=(int)($f[$id]??0);$src=tdbs_face($id,$o['photo']);
        echo '<tr><th>'.esc_html($o['name']).'</th><td class="tdbs-face-row"><span class="tdbs-face-preview">'.($src!==''?'<img src="'.esc_attr($src).'" alt="" style="width:56px;height:56px;border-radius:50%;object-fit:cover;vertical-align:middle">':'<span style="display:inline-grid;place-items:center;width:56px;height:56px;border-radius:50%;background:#eee;vertical-align:middle">'.esc_html(tdbs_initials($o['name'])).'</span>').'</span> '
            .'<input type="hidden" name="face['.esc_attr($id).']" value="'.esc_attr((string)$mine).'" data-face> <button type="button" class="button" data-face-pick>Scegli dalla libreria</button> <button type="button" class="button" data-face-clear'.($mine?'':' hidden').'>Togli, usa quella del programma</button>'
            .($mine?'':($o['photo']!==''?' <span class="description">dal programma</span>':' <span class="description">nessuna foto</span>')).'</td></tr>';
    }
    echo '</table>';submit_button('Salva le foto','secondary');echo '</form></div>';
    echo '<script>document.addEventListener("click",function(e){var b=e.target.closest("[data-face-pick],[data-face-clear]");if(!b||!window.wp||!wp.media)return;var row=b.closest("td"),input=row.querySelector("[data-face]"),prev=row.querySelector(".tdbs-face-preview"),clear=row.querySelector("[data-face-clear]");'
        .'if(b.hasAttribute("data-face-clear")){input.value="0";clear.hidden=true;prev.innerHTML="";return;}'
        .'var frame=wp.media({title:"Foto dell\u2019operatore",button:{text:"Usa questa foto"},multiple:false,library:{type:"image"}});'
        .'frame.on("select",function(){var a=frame.state().get("selection").first().toJSON();input.value=a.id;var u=(a.sizes&&a.sizes.thumbnail?a.sizes.thumbnail.url:a.url);prev.innerHTML=\'<img src="\'+u+\'" alt="" style="width:56px;height:56px;border-radius:50%;object-fit:cover;vertical-align:middle">\';clear.hidden=false;});frame.open();});</script>';
}
add_action('admin_post_tdbs_faces_save',function(){
    tdbs_admin_check('tdbs_faces_save');
    $out=array();
    foreach ((array)($_POST['face']??array()) as $id=>$v) if (preg_match('/^[a-f0-9-]{1,40}$/',(string)$id) && absint($v) && get_post_type(absint($v))==='attachment') $out[(string)$id]=absint($v);
    update_option('tdbs_faces',$out,false);
    tdbs_flash('Foto degli operatori salvate.','success');tdbs_back();
});
function tdbs_initials($name) {$i=mb_strtoupper(mb_substr($name,0,1));if (preg_match('/\s(\S)/u',$name,$m)) $i.=mb_strtoupper($m[1]);return $i;}
function tdbs_agenda_grid($a,$days,$snap,$only='') {
    $week=count($days)>1;$ops=array();
    foreach ((array)$a['operators'] as $o) if (is_array($o)) $ops[(string)$o['id']]=array('name'=>(string)$o['name'],'color'=>(int)($o['color']??0),'photo'=>tdbs_face((string)$o['id'],(string)($o['photo']??'')));
    $items=array();
    foreach ((array)$a['bookings'] as $b) {
        $d=substr((string)$b['start'],0,10);$rid=(string)($b['resource']??'');
        if (!in_array($d,$days,true) || ($only!=='' && $rid!==$only)) continue;
        if ($rid!=='' && !isset($ops[$rid])) $ops[$rid]=array('name'=>(string)($b['operator']??''),'color'=>0,'photo'=>'');
        $items[]=$b;
    }
    $none=false;foreach ($items as $x) if ((string)($x['resource']??'')==='') {$none=true;break;}
    list($lo,$hi)=tdbs_agenda_hours($snap,$days,$items);$rows=(int)(($hi-$lo)/15);
    $name=function($d,$f){return wp_date($f,strtotime($d.' 12:00 UTC'),new DateTimeZone('UTC'));};
    // the columns: the operators of the day (and "Senza operatore" when needed), or the seven days
    $cols=array();
    if ($week) foreach ($days as $d) $cols[]=array('key'=>$d,'label'=>$name($d,'D j'),'sub'=>'','day'=>$d);
    else {foreach ($ops as $id=>$o) if ($only==='' || $id===$only) $cols[]=array('key'=>$id,'label'=>$o['name'],'sub'=>'','day'=>$days[0],'op'=>$o);if ($none || !$cols) $cols[]=array('key'=>'-','label'=>$ops?'Senza operatore':'Prenotazioni','sub'=>'','day'=>$days[0]);}
    // the operators as a drop-down with their faces: one of them and his calendar, or everyone
    $faces='';
    if ($ops) {
        $av=function($o,$cls='') {return $o['photo']!==''?'<img class="tdbs-ag-avatar'.$cls.'" src="'.esc_attr($o['photo']).'" alt="">':'<span class="tdbs-ag-avatar'.$cls.'" aria-hidden="true">'.esc_html(tdbs_initials($o['name'])).'</span>';};
        $all='<span class="tdbs-ag-avatar is-small is-all" aria-hidden="true">'.count($ops).'</span>';
        $cur=$only!=='' && isset($ops[$only])?'<span class="op-'.$ops[$only]['color'].'">'.$av($ops[$only],' is-small').'</span><span>'.esc_html($ops[$only]['name']).'</span>':$all.'<span>Tutti gli operatori</span>';
        $faces='<details class="tdbs-ag-pick" data-pick><summary aria-label="Operatore">'.$cur.'<span class="tdbs-ag-chev" aria-hidden="true"></span></summary><ul role="list">'
            .'<li><a href="'.esc_url(remove_query_arg(array('operatore','nuovo'))).'"'.($only===''?' aria-current="true"':'').'>'.$all.'<span>Tutti gli operatori</span></a></li>';
        foreach ($ops as $id=>$o) $faces.='<li class="op-'.$o['color'].'"><a href="'.esc_url(add_query_arg('operatore',$id,remove_query_arg('nuovo'))).'"'.($only===$id?' aria-current="true"':'').'>'.$av($o,' is-small').'<span>'.esc_html($o['name']).'</span></a></li>';
        $faces.='</ul></details>';
    }
    // the grid: the hours, then one column per key with its bookings placed by time; overlapping ones side by side
    $html='<div class="tdbs-ag-scroll" role="region" aria-label="Agenda" tabindex="0"><div class="tdbs-ag'.($week?' is-week':'').'" style="--rows:'.$rows.'">';
    $html.='<div class="tdbs-ag-head"><div class="tdbs-ag-corner"></div>';
    foreach ($cols as $c) $html.='<div class="tdbs-ag-th'.(isset($c['op'])?' op-'.$c['op']['color']:'').'">'.(isset($c['op'])?($c['op']['photo']!==''?'<img class="tdbs-ag-avatar is-small" src="'.esc_attr($c['op']['photo']).'" alt="">':'<span class="tdbs-ag-avatar is-small" aria-hidden="true">'.esc_html(tdbs_initials($c['op']['name'])).'</span>'):'').'<span>'.esc_html($c['label']).'</span></div>';
    $html.='</div><div class="tdbs-ag-body"><div class="tdbs-ag-hours">';
    for ($h=$lo;$h<$hi;$h+=60) $html.='<span>'.sprintf('%02d:00',$h/60).'</span>';
    $html.='</div>';
    $m=function($t){return (int)substr($t,0,2)*60+(int)substr($t,3,2);};
    try {$tz=new DateTimeZone((string)($a['timezone']??'Europe/Rome'));} catch (Exception $e) {$tz=wp_timezone();}
    $now=new DateTime('now',$tz);$today=$now->format('Y-m-d');$nowmin=(int)$now->format('H')*60+(int)$now->format('i');
    foreach ($cols as $c) {
        $mine=array();
        foreach ($items as $x) {
            $rid=(string)($x['resource']??'');$d=substr((string)$x['start'],0,10);
            if ($week?$d===$c['day']:($c['key']==='-'?$rid==='':$rid===$c['key'])) $mine[]=$x;
        }
        usort($mine,function($x,$y){return strcmp($x['start'],$y['start']);});
        // lanes: an item goes in the first lane free at its start
        $lanes=array();$placed=array();
        foreach ($mine as $x) {
            $s=$m(substr($x['start'],11,5));$e=max($s+15,$m(substr($x['end'],11,5)));$l=0;
            while (isset($lanes[$l]) && $lanes[$l]>$s) $l++;
            $lanes[$l]=$e;$placed[]=array($x,$s,$e,$l);
        }
        $n=max(1,count($lanes));
        $html.='<div class="tdbs-ag-col'.($c['day']===$today?' is-today':'').'" data-day="'.esc_attr($c['day']).'">';
        if ($c['day']===$today && $nowmin>=$lo && $nowmin<=$hi) $html.='<span class="tdbs-ag-now" style="--at:'.(($nowmin-$lo)/15).'"></span>';
        foreach ($placed as $p) {
            list($x,$s,$e,$l)=$p;$top=max(0,($s-$lo)/15);$h=max(1,min($rows-$top,($e-$s)/15));
            $op=isset($ops[(string)($x['resource']??'')])?$ops[(string)$x['resource']]:array('color'=>0);
            $detail=array('quando'=>$name(substr($x['start'],0,10),'l j F').' · '.substr($x['start'],11,5).'–'.substr($x['end'],11,5),'servizio'=>(string)$x['service'],'con'=>(string)($x['operator']??''),'cliente'=>(string)($x['client']??''),'telefono'=>(string)($x['phone']??''),'note'=>(string)($x['notes']??''),'stato'=>(string)($x['status']??''));
            $html.='<button type="button" class="tdbs-ag-item op-'.(int)$op['color'].(!empty($x['pending'])?' is-pending':'').($h<=1?' is-short':'').'" style="--top:'.$top.';--h:'.$h.';--lane:'.$l.';--lanes:'.$n.'" data-detail="'.esc_attr(wp_json_encode($detail,JSON_UNESCAPED_UNICODE)).'">'
                .'<strong>'.esc_html(substr($x['start'],11,5)).'</strong> <span class="tdbs-ag-who">'.esc_html((string)($x['client']?:'Senza nome')).'</span><small>'.esc_html($x['service'].($week && !empty($x['operator'])?' · '.$x['operator']:'')).'</small></button>';
        }
        $html.='</div>';
    }
    $html.='</div></div></div>';
    $sheet='<dialog class="tdbs-ag-sheet" aria-label="Prenotazione"><div class="tdbs-ag-sheet-in"><button type="button" class="tdbs-ag-close" data-close aria-label="Chiudi">×</button><dl data-detail-list></dl></div></dialog>';
    return ($week?'':'<h3 class="tdbs-ag-title">'.esc_html($name($days[0],'l j F')).'</h3>').$faces.$html.$sheet;
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
        .$link('<span aria-hidden="true">›</span><span class="screen-reader-text">Avanti</span>',array('vista'=>$v,'giorno'=>$shift($day,$step)))
        .(isset($_GET['tdbs_app'])?'':'<a class="button" href="'.esc_url(tdbs_app_url()).'">Apri come app</a>').$out.'</nav>';
    $days=array($day);
    if ($week) {$first=$shift($day,-((int)gmdate('N',strtotime($day.' 12:00 UTC'))-1));$days=array();for ($i=0;$i<7;$i++) $days[]=$shift($first,$i);}
    $at=(int)($stored['received']??0);
    // 2.19.0: Nuovo appuntamento; once saved, the page opens again on its day with this line
    $saved=($_GET['nuovo']??'')==='1'?'<p class="tdbs-agenda-status" role="status">Appuntamento salvato: qui sotto è «Da sincronizzare» finché il PC non lo prende, entro 2 minuti con il PC acceso.</p>':'';
    $only=preg_match('/^[a-f0-9-]{1,40}$/',(string)($_GET['operatore']??''))?(string)$_GET['operatore']:'';
    wp_enqueue_script('tdbs-agenda',plugins_url('agenda.js',__FILE__),array(),TDBS_VERSION,true);
    return '<div class="tdbs-agenda"><p class="tdbs-agenda-updated">Aggiornata alle '.esc_html(wp_date('H:i',$at)).' del '.esc_html(wp_date('d/m',$at)).'</p>'.$saved.tdbs_agenda_form().$bar.'<div class="tdbs-agenda-body">'.tdbs_agenda_grid($a,$days,tdbs_pc_snapshot(),$only).'</div></div>';
}
add_action('init',function(){add_shortcode('tdbs_agenda','tdbs_agenda_shortcode');});

/* ---------- 2.22.1 l'agenda come app sul telefono: a page of its own (?tdbs_app=1) without the theme, with the site's name,
   logo and icon and the business's colour, a web app manifest (?tdbs_manifest=1) and a service worker (?tdbs_sw=1, from
   the root so it may control the page): Chrome offers "Installa app", Safari "Aggiungi alla schermata Home", and the agenda
   opens full screen like an app; without connection it shows the last agenda seen. The password and the session are the
   agenda's own; nothing else of the site is cached. */
function tdbs_app_url() {return add_query_arg('tdbs_app','1',home_url('/'));}
function tdbs_app_color() {$c=(string)(tdbs_pc_snapshot()['color']??'');return preg_match('/^#[0-9a-fA-F]{6}$/',$c)?$c:'#6d4aff';}
/** The icons of the app: the site icon (Aspetto › Personalizza › Identità del sito), else the theme's logo when big enough. */
function tdbs_app_icons() {
    $out=array();
    foreach (array(192,512) as $s) if ($u=get_site_icon_url($s)) $out[]=array('src'=>$u,'sizes'=>$s.'x'.$s,'type'=>(string)(wp_check_filetype($u)['type']?:'image/png'),'purpose'=>'any');
    if (!$out && ($id=(int)get_theme_mod('custom_logo')) && ($img=wp_get_attachment_image_src($id,'full')) && min((int)$img[1],(int)$img[2])>=144)
        $out[]=array('src'=>$img[0],'sizes'=>$img[1].'x'.$img[2],'type'=>(string)(get_post_mime_type($id)?:'image/png'),'purpose'=>'any');
    return $out;
}
add_action('template_redirect',function(){
    if (isset($_GET['tdbs_manifest'])) {
        nocache_headers();header('Content-Type: application/manifest+json; charset=UTF-8');
        $name=(string)get_bloginfo('name');
        echo wp_json_encode(array('name'=>$name.' · Agenda','short_name'=>mb_substr($name,0,12),'start_url'=>tdbs_app_url(),'scope'=>home_url('/'),'display'=>'standalone','orientation'=>'portrait','lang'=>'it',
            'background_color'=>'#ffffff','theme_color'=>tdbs_app_color(),'icons'=>tdbs_app_icons()),JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE);
        exit;
    }
    if (isset($_GET['tdbs_sw'])) {
        nocache_headers();header('Content-Type: application/javascript; charset=UTF-8');header('Service-Worker-Allowed: /');
        readfile(__DIR__.'/app-sw.js');exit;
    }
    if (isset($_GET['tdbs_app'])) {tdbs_app_page();exit;}
},0);
function tdbs_app_page() {
    if (!defined('DONOTCACHEPAGE')) define('DONOTCACHEPAGE',true);
    nocache_headers();header('Cache-Control: no-store, private');header('X-Robots-Tag: noindex, nofollow');status_header(200);
    if (empty(tdbs_pc()['agenda'])) {status_header(404);echo '<!DOCTYPE html><meta charset="utf-8"><p>Agenda non attiva.</p>';return;}
    $name=(string)get_bloginfo('name');$color=tdbs_app_color();$icon=get_site_icon_url(192);$touch=get_site_icon_url(180);
    $logo=($id=(int)get_theme_mod('custom_logo'))?wp_get_attachment_image_src($id,'medium'):null;
    $css=plugins_url('widget.css',__FILE__).'?ver='.TDBS_VERSION;$js=plugins_url('agenda.js',__FILE__).'?ver='.TDBS_VERSION;
    $body=tdbs_agenda_shortcode();  // the same agenda; its script is printed here (no theme, no wp_footer)
    echo '<!DOCTYPE html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Agenda · '.esc_html($name).'</title>'
        .'<link rel="manifest" href="'.esc_url(add_query_arg('tdbs_manifest','1',home_url('/'))).'"><meta name="theme-color" content="'.esc_attr($color).'"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="'.esc_attr(mb_substr($name,0,12)).'">'
        .($touch?'<link rel="apple-touch-icon" href="'.esc_url($touch).'">':'').($icon?'<link rel="icon" href="'.esc_url($icon).'">':'')
        .'<link rel="stylesheet" href="'.esc_url($css).'"></head><body class="tdbs-app">'
        .'<header class="tdbs-app-bar" style="background:'.esc_attr($color).'">'.($logo?'<img src="'.esc_url($logo[0]).'" alt="">':($icon?'<img src="'.esc_url($icon).'" alt="">':'')).'<strong>'.esc_html($name).'</strong><span>Agenda</span></header>'
        .'<main class="tdbs-app-main">'.$body.'</main>'
        .'<div class="tdbs-app-install" role="note" hidden><button type="button" data-install>Installa l’app sul telefono</button><span data-ios hidden>Per averla sulla schermata Home: <strong>Condividi</strong> › <strong>Aggiungi alla schermata Home</strong></span><button type="button" class="tdbs-app-later" data-later aria-label="Chiudi">×</button></div>'
        .'<script src="'.esc_url($js).'" defer></script><script>'.tdbs_app_script().'</script></body></html>';
}
function tdbs_app_script() {
    return 'if("serviceWorker" in navigator)navigator.serviceWorker.register('.wp_json_encode(add_query_arg('tdbs_sw','1',home_url('/'))).').catch(function(){});'
        .'if(document.querySelector(".tdbs-agenda-login")&&window.caches)caches.delete("tdbs-app-1");'  // closed: the last agenda is not kept on the phone
        .'(function(){var box=document.querySelector(".tdbs-app-install"),btn=box.querySelector("[data-install]"),ios=box.querySelector("[data-ios]");'
        .'var standalone=matchMedia("(display-mode: standalone)").matches||navigator.standalone===true;var later=0;try{later=+sessionStorage.getItem("tdbs-app-later")||0;}catch(e){}'
        .'if(standalone||later)return;var ev=null,closed=false;'
        .'addEventListener("beforeinstallprompt",function(e){e.preventDefault();ev=e;if(!closed)box.hidden=false;});'
        .'btn.addEventListener("click",function(){if(!ev)return;ev.prompt();ev.userChoice.then(function(){ev=null;box.hidden=true;});});'
        .'if(/iphone|ipad|ipod/i.test(navigator.userAgent)){btn.hidden=true;ios.hidden=false;box.hidden=false;}'
        .'box.querySelector("[data-later]").addEventListener("click",function(){closed=true;box.hidden=true;try{sessionStorage.setItem("tdbs-app-later","1");}catch(e){}});})();';
}

/* the settings page: Modalità PC (sincronizzazione) */
function tdbs_pc_settings($post) {
    $p=tdbs_pc();$snap=tdbs_pc_snapshot();
    global $wpdb;$waiting=!empty($p['secret'])?(int)$wpdb->get_var('SELECT COUNT(*) FROM '.tdbs_pc_table()):0;
    echo '<div class="card" style="max-width:860px"><h2>Modalità PC (sincronizzazione)</h2><p>Per il programma installato su un PC dell’attività, spesso spento e non raggiungibile da Internet. Quando il PC è acceso, ogni 2 minuti manda a questo sito servizi, operatori e orari liberi e prende le richieste arrivate: il modulo funziona anche a PC spento.</p>'
        .'<ol><li>Nel programma apri <em>Impostazioni e sito › Sito WordPress › Modalità PC</em> (la vede chi amministra il programma). Scrivi l’indirizzo di questo sito, <code>'.esc_html(tdbs_site_origin()).'</code>, premi <em>Salva</em>, poi <em>Genera la chiave</em> e <em>Copia</em>.</li>'
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
        .'<li>Dai la password solo a chi deve vedere l’agenda. L’agenda arriva alla sincronizzazione dopo, entro 2 minuti con il PC acceso.</li>'
        .'<li><strong>Sul telefono, come app</strong>: apri <code>'.esc_html(tdbs_app_url()).'</code> (o <em>Apri come app</em> nella pagina) e installala: Chrome propone <em>Installa l’app</em>, su iPhone <em>Condividi › Aggiungi alla schermata Home</em>. Nome e icona sono quelli del sito ('.(tdbs_app_icons()?'icona del sito impostata':'<strong>manca l’icona del sito</strong>: senza, Chrome non propone l’installazione; impostala in Aspetto › Personalizza › Identità del sito').'), il colore quello dell’attività nel programma. Senza connessione mostra l’ultima agenda vista.</li></ol>'
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
/** 2.22.0: «Colore + Taglio + Piega»: the first service with the ones added to the visit. */
function tdbs_visit_name($snap,$sv,$pieces) {
    $name=(string)$sv['name'];
    foreach ((array)$pieces as $x) {$o=tdbs_pc_service($snap,(string)($x['service']??''));if ($o) $name.=' + '.$o['name'];}
    return $name;
}
function tdbs_site_info($snap,$sv,$option,$rid,$date,$time,$end,$client,$pieces=array()) {
    $tz=tdbs_tz();$name=(string)$sv['name'];$who='';
    foreach ((array)($sv['options']??array()) as $o) if (($o['id']??'')===$option) $name.=' · '.$o['name'];
    foreach ((array)$pieces as $x) {$o=tdbs_pc_service($snap,(string)($x['service']??''));if ($o) $name.=' + '.$o['name'];}
    foreach ((array)($sv['resources']??array()) as $r) if ($rid!=='' && ($r['id']??'')===$rid) $who=(string)$r['name'];
    return array('first_name'=>$client['first_name'],'last_name'=>$client['last_name'],'email'=>$client['email'],'phone'=>$client['phone'],'notices'=>!empty($client['notices']),
        'service'=>$name,'service_id'=>(string)$sv['id'],'operator'=>$who,'location'=>(string)($sv['location']??''),
        'start'=>(new DateTime($date.' '.$time,$tz))->getTimestamp(),'end'=>(new DateTime($date.' '.$end,$tz))->getTimestamp());
}
/* ---------- 2.22.2 updates from the public showcase (github.com/Giuseppe-sciarra/TD-Booking-Suite-per-Windows): the
   Windows release attaches td-booking-suite-<versione>.zip there with the MSI; WordPress shows the new version in Plugin
   and installs it like any other update. Asked at most every 12 hours, without any key: the releases are public. */
const TDBS_REPO='Giuseppe-sciarra/TD-Booking-Suite-per-Windows';
function tdbs_latest() {
    $hit=get_site_transient('tdbs_latest');
    if (is_array($hit) && (int)($hit['at']??0)>time()-12*HOUR_IN_SECONDS) return $hit;
    $hit=array('at'=>time(),'version'=>'','zip'=>'','notes'=>'','url'=>'');
    $r=wp_remote_get('https://api.github.com/repos/'.TDBS_REPO.'/releases/latest',array('timeout'=>10,'headers'=>array('Accept'=>'application/vnd.github+json','User-Agent'=>'TD-Booking-Suite/'.TDBS_VERSION)));
    $d=is_wp_error($r)?null:json_decode((string)wp_remote_retrieve_body($r),true);
    if (is_array($d) && empty($d['draft']) && empty($d['prerelease'])) {
        $v=ltrim((string)($d['tag_name']??''),'v');
        foreach ((array)($d['assets']??array()) as $a) if (preg_match('/^td-booking-suite-[0-9.]+\.zip$/',(string)($a['name']??''))) $hit['zip']=(string)($a['browser_download_url']??'');
        if ($hit['zip']!=='' && preg_match('/^\d+\.\d+\.\d+$/',$v)) {$hit['version']=$v;$hit['notes']=(string)($d['body']??'');$hit['url']=(string)($d['html_url']??'');}
    }
    set_site_transient('tdbs_latest',$hit,12*HOUR_IN_SECONDS);
    return $hit;
}
add_action('load-update-core.php',function(){if (isset($_GET['force-check'])) delete_site_transient('tdbs_latest');});
add_filter('pre_set_site_transient_update_plugins',function($t){
    if (!is_object($t)) return $t;
    $l=tdbs_latest();$file=plugin_basename(__FILE__);
    $row=array('id'=>$file,'slug'=>'td-booking-suite','plugin'=>$file,'url'=>'https://github.com/'.TDBS_REPO,'icons'=>array(),'banners'=>array(),'requires_php'=>'8.0');
    if ($l['version']!=='' && version_compare($l['version'],TDBS_VERSION,'>')) {$t->response[$file]=(object)($row+array('new_version'=>$l['version'],'package'=>$l['zip']));unset($t->no_update[$file]);}
    else {unset($t->response[$file]);$t->no_update[$file]=(object)($row+array('new_version'=>TDBS_VERSION,'package'=>''));}
    return $t;
});
add_filter('plugins_api',function($res,$action,$args){
    if ($action!=='plugin_information' || (string)($args->slug??'')!=='td-booking-suite') return $res;
    $l=tdbs_latest();
    return (object)array('name'=>'TD Booking Suite — Prenotazioni','slug'=>'td-booking-suite','version'=>$l['version']!==''?$l['version']:TDBS_VERSION,'author'=>'Tastiere Digitali','homepage'=>'https://github.com/'.TDBS_REPO,
        'requires'=>'6.2','requires_php'=>'8.0','download_link'=>$l['zip'],'sections'=>array('description'=>'Il modulo di prenotazione di TD Booking Suite nel sito, la modalità PC, l’agenda e l’archivio.','changelog'=>$l['notes']!==''?wp_kses_post(nl2br(esc_html($l['notes']))):'Vedi il rilascio su GitHub.'));
},10,3);
require_once __DIR__.'/mails.php';
