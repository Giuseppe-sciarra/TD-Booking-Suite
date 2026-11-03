<?php
/**
 * Plugin Name: TD Booking Suite — Prenotazioni
 * Description: Mostra nel sito il modulo di prenotazione di TD Booking Suite (Cloud o Private). Si collega al portale con un codice e si configura da solo. Modalità PC (2.17.0): con il programma su un PC spesso spento, il modulo funziona dai dati che il PC manda e le richieste restano cifrate nel sito solo finché il PC non le prende. Agenda riservata (2.17.1): [tdbs_agenda] mostra al titolare e ai collaboratori le prenotazioni, cifrate dal PC e leggibili solo nel browser con la password.
 * Version: 2.17.1
 * Requires at least: 6.2
 * Requires PHP: 8.0
 * Author: Tastiere Digitali
 * License: GPL-2.0-or-later
 */
defined('ABSPATH') || exit;

const TDBS_VERSION='2.17.1';

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
    echo '<div class="card" style="max-width:860px"><h2>'.($connected?'Collega a un’altra attività':'Collegati al portale').'</h2><p>Nel portale apri <em>Configura l’attività › Sito WordPress</em> e genera il codice di collegamento. Il plugin autorizza questo sito, legge la configurazione e, se vuoi, crea la pagina Prenota.</p>';
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
    register_rest_route('tdbs/v1','/agenda',array('methods'=>'GET','callback'=>'tdbs_agenda_api','permission_callback'=>'__return_true'));
});

/** From the PC only: signature over "time.body", 5 minutes of window, each signature once. */
function tdbs_pc_sync(WP_REST_Request $request) {
    global $wpdb;
    if (empty(tdbs_pc()['secret'])) return tdbs_pc_error('Modalità PC non configurata.',404);
    $body=(string)$request->get_body();$ts=(string)$request->get_header('x-tdbs-time');$sig=(string)$request->get_header('x-tdbs-signature');
    $seen=get_option('tdbs_pc_seen',array());$seen=is_array($seen)?$seen:array();
    if (strlen($body)>5000000 || !ctype_digit($ts) || abs(time()-(int)$ts)>300 || !hash_equals(tdbs_pc_sign($ts,$body),$sig) || in_array($sig,$seen,true)) return tdbs_pc_error('Firma non valida.',401);
    update_option('tdbs_pc_seen',array_slice(array_merge($seen,array($sig)),-100),false);
    $data=json_decode($body,true);
    if (!is_array($data)) return tdbs_pc_error('Richiesta non valida.');
    $table=tdbs_pc_table();
    foreach (array_slice((array)($data['ack']??array()),0,500) as $id) if (is_string($id) && preg_match('/^[a-f0-9]{32}$/',$id)) $wpdb->delete($table,array('id'=>$id));
    // without a snapshot nothing changed on the PC since the last one: the times are still those, checked now
    $snap=isset($data['snapshot']) && is_array($data['snapshot']) ? $data['snapshot'] : tdbs_pc_snapshot();
    if ($snap) {$snap['received']=time();update_option('tdbs_pc_snapshot',$snap,false);}
    // 2.17.1: the private agenda, sealed by the PC; a list sent replaces the old one, so a revoked access goes away
    $agenda=get_option('tdbs_agenda',array());$agenda=is_array($agenda)?$agenda:array();
    if (isset($data['agenda']) && is_array($data['agenda'])) $agenda['entries']=tdbs_agenda_clean($data['agenda']);
    if (!empty($agenda['entries'])) {$agenda['received']=time();update_option('tdbs_agenda',$agenda,false);} else delete_option('tdbs_agenda');
    $out=array();
    foreach ((array)$wpdb->get_results("SELECT id,slot,data FROM $table ORDER BY created LIMIT 200",ARRAY_A) as $row) {
        $slot=json_decode($row['slot'],true);$client=tdbs_pc_open($row['data']);
        if (is_array($slot) && is_array($client)) $out[]=array_merge($slot,$client,array('id'=>$row['id']));
    }
    // the answer is signed as a string inside the envelope: WordPress encodes the envelope as it likes
    $inner=wp_json_encode(array('ok'=>true,'bookings'=>$out));$now=(string)time();
    $response=new WP_REST_Response(array('body'=>$inner,'time'=>$now,'sig'=>tdbs_pc_sign($now,$inner)),200);
    $response->header('Cache-Control','no-store');
    return $response;
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
/** A few requests per address and per hour, and a ceiling for the whole site. */
function tdbs_pc_throttle() {
    $key='tdbs_rl_'.md5((string)($_SERVER['REMOTE_ADDR']??'').wp_salt('nonce'));
    $one=(int)get_transient($key);$all=(int)get_transient('tdbs_rl_all');
    if ($one>=10 || $all>=60) return false;
    set_transient($key,$one+1,HOUR_IN_SECONDS);set_transient('tdbs_rl_all',$all+1,HOUR_IN_SECONDS);
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

/* ---------- 2.17.1 Agenda riservata: for each access the PC sends its bookings sealed with a key derived from its password
   (PBKDF2-HMAC-SHA256 + AES-256-GCM); the site never has the password and never decrypts them: agenda.js does, in the
   browser. The requests taken here and not yet by the PC are sealed for each access with its RSA public key. */
function tdbs_agenda_clean($list) {
    $out=array();
    foreach (array_slice($list,0,20) as $e) {
        if (!is_array($e) || !preg_match('/^[a-f0-9]{32}$/',(string)($e['id']??''))) continue;
        foreach (array('salt','nonce','ct','public') as $k) if (!is_string($e[$k]??null) || base64_decode($e[$k],true)===false) continue 2;
        $out[]=array('id'=>$e['id'],'label'=>sanitize_text_field((string)($e['label']??'')),'generated'=>sanitize_text_field((string)($e['generated']??'')),'iterations'=>max(200000,(int)($e['iterations']??0)),
            'salt'=>$e['salt'],'nonce'=>$e['nonce'],'ct'=>$e['ct'],'public'=>$e['public'],
            'resources'=>array_values(array_filter((array)($e['resources']??array()),function($r){return is_string($r) && preg_match('/^[a-f0-9-]{36}$/i',$r);})));
    }
    return $out;
}
/** The site's requests not yet taken by the PC, as the agenda shows them: "in attesa di conferma". */
function tdbs_agenda_pending() {
    global $wpdb;$out=array();
    if (empty(tdbs_pc()['secret'])) return $out;
    $snap=tdbs_pc_snapshot();
    foreach ((array)$wpdb->get_results('SELECT id,slot,data FROM '.tdbs_pc_table().' ORDER BY created',ARRAY_A) as $row) {
        $slot=json_decode($row['slot'],true);$client=tdbs_pc_open($row['data']);
        if (!is_array($slot) || !is_array($client)) continue;
        $sv=tdbs_pc_service($snap,(string)($slot['service']??''));$name=$sv?(string)$sv['name']:'Richiesta dal sito';$who='';
        foreach ((array)($sv['options']??array()) as $o) if (($o['id']??'')===($slot['option']??'')) $name.=' · '.$o['name'];
        foreach ((array)($sv['resources']??array()) as $r) if (($r['id']??'')===($slot['resource']??'')) $who=(string)$r['name'];
        $out[]=array('id'=>$row['id'],'start'=>$slot['date'].'T'.$slot['time'],'end'=>$slot['date'].'T'.$slot['end'],'service'=>$name,'resource'=>(string)$slot['resource'],'operator'=>$who,
            'status'=>'In attesa di conferma · dal sito','pending'=>true,'client'=>trim($client['first_name'].' '.$client['last_name']),'phone'=>$client['phone'],'notes'=>$client['notes']);
    }
    return $out;
}
/** AES-256-GCM with a fresh key, the key wrapped with RSA-OAEP (SHA-1, as WebCrypto reads openssl's padding). */
function tdbs_agenda_wrap($rows,$public) {
    $pem="-----BEGIN PUBLIC KEY-----\n".chunk_split($public,64,"\n")."-----END PUBLIC KEY-----\n";
    $key=random_bytes(32);$iv=random_bytes(12);$tag='';
    $ct=openssl_encrypt(wp_json_encode($rows),'aes-256-gcm',$key,OPENSSL_RAW_DATA,$iv,$tag,'',16);
    if ($ct===false || !openssl_public_encrypt($key,$wrapped,$pem,OPENSSL_PKCS1_OAEP_PADDING)) return null;
    return array('key'=>base64_encode($wrapped),'iv'=>base64_encode($iv),'ct'=>base64_encode($ct.$tag));
}
/** Public, ciphertext only (no names of the accesses either), a few calls per address and per hour. */
function tdbs_agenda_api() {
    $agenda=get_option('tdbs_agenda',array());
    if (empty($agenda['entries'])) return tdbs_pc_error('Agenda non disponibile.',404);
    $key='tdbs_ag_'.md5((string)($_SERVER['REMOTE_ADDR']??'').wp_salt('nonce'));$n=(int)get_transient($key);
    if ($n>=30) return tdbs_pc_error('Troppi tentativi: riprova tra un’ora.',429);
    set_transient($key,$n+1,HOUR_IN_SECONDS);
    $pending=tdbs_agenda_pending();$out=array();
    foreach ($agenda['entries'] as $e) {
        $mine=array_values(array_filter($pending,function($p)use($e){return !$e['resources'] || in_array($p['resource'],$e['resources'],true);}));
        $out[]=array('salt'=>$e['salt'],'nonce'=>$e['nonce'],'ct'=>$e['ct'],'iterations'=>$e['iterations'],'pending'=>$mine?tdbs_agenda_wrap($mine,$e['public']):null);
    }
    $response=new WP_REST_Response(array('ok'=>true,'checked'=>(int)($agenda['received']??0),'entries'=>$out),200);
    $response->header('Cache-Control','no-store');
    return $response;
}
function tdbs_agenda_shortcode() {
    wp_enqueue_style('tdbs-widget',plugins_url('widget.css',__FILE__),array(),TDBS_VERSION);
    wp_enqueue_script('tdbs-agenda',plugins_url('agenda.js',__FILE__),array(),TDBS_VERSION,true);
    return '<div class="tdbs-agenda" data-rest="'.esc_attr(esc_url_raw(rest_url('tdbs/v1/'))).'"><form class="tdbs-agenda-login">'
        .'<p><label>Password dell’agenda<br><input type="password" name="password" required autocomplete="current-password"></label></p>'
        .'<p><label><input type="checkbox" name="remember"> Ricordala finché non chiudo la scheda</label></p>'
        .'<p><button type="submit">Apri l’agenda</button></p><p class="tdbs-agenda-status" role="status" aria-live="polite"></p></form><div class="tdbs-agenda-view" hidden></div></div>';
}
add_action('init',function(){add_shortcode('tdbs_agenda','tdbs_agenda_shortcode');});

/* the settings page: Modalità PC (sincronizzazione) */
function tdbs_pc_settings($post) {
    $p=tdbs_pc();$snap=tdbs_pc_snapshot();
    global $wpdb;$waiting=!empty($p['secret'])?(int)$wpdb->get_var('SELECT COUNT(*) FROM '.tdbs_pc_table()):0;
    echo '<div class="card" style="max-width:860px"><h2>Modalità PC (sincronizzazione)</h2><p>Per il programma installato su un PC dell’attività, spesso spento e non raggiungibile da Internet. Quando è acceso il PC manda al sito servizi, operatori e orari liberi, e prende le richieste arrivate: il modulo nel sito funziona anche a PC spento. Nel programma, <em>Configura l’attività › Sito WordPress · modalità PC</em>: indirizzo del sito <code>'.esc_html(tdbs_site_origin()).'</code> e chiave da incollare qui.</p>';
    if (!empty($snap['received'])) echo '<p>Ultimi dati dal PC: '.esc_html(wp_date('d/m/Y H:i',(int)$snap['received'])).', '.count((array)($snap['services']??array())).' servizi. Richieste in attesa del PC: '.$waiting.'.</p>';
    echo '<form method="post" action="'.$post.'"><input type="hidden" name="action" value="tdbs_pc_save">';wp_nonce_field('tdbs_pc_save');
    echo '<table class="form-table"><tr><th>Modalità</th><td><label><input type="checkbox" name="on" value="1"'.checked(!empty($p['on']),true,false).'> Usa la modalità PC per <code>[td_booking_suite]</code> al posto del modulo dal portale</label></td></tr>';
    echo '<tr><th><label for="tdbs-secret">Chiave dal programma</label></th><td><input type="password" class="regular-text" id="tdbs-secret" name="secret" autocomplete="off" placeholder="'.(!empty($p['secret'])?'Chiave salvata: lascia vuoto per tenerla':'Incolla la chiave').'"></td></tr>';
    echo '<tr><th><label for="tdbs-age">Dati validi per</label></th><td><input type="number" min="1" max="60" id="tdbs-age" name="max_age" value="'.esc_attr((string)($p['max_age']??7)).'"> giorni <p class="description">Se il PC non si fa sentire da più tempo, il modulo mostra “contattaci” invece degli orari.</p></td></tr></table>';
    $agenda=get_option('tdbs_agenda',array());
    echo '<p>Agenda riservata: '.(!empty($agenda['entries'])?'accessi '.esc_html(implode(', ',array_column($agenda['entries'],'label'))).'. Pagina con lo shortcode <code>[tdbs_agenda]</code>: si apre con la password data dal programma.':'nessun accesso; si creano nel programma, nella stessa card.').'</p>';
    submit_button('Salva la modalità PC','secondary');echo '</form></div>';
}
add_action('admin_post_tdbs_pc_save',function(){
    global $wpdb;
    tdbs_admin_check('tdbs_pc_save');
    $old=tdbs_pc();$secret=trim((string)wp_unslash($_POST['secret']??''));
    $new=array('on'=>!empty($_POST['on']),'secret'=>(string)($old['secret']??''),'max_age'=>max(1,min(60,absint($_POST['max_age']??7))));
    if ($secret!=='') {
        if (!preg_match('/^[A-Za-z0-9_-]{32,100}$/',$secret)) {tdbs_flash('La chiave non è valida: copiala di nuovo dal programma.');tdbs_back();}
        if ($secret!==$new['secret'] && $new['secret']!=='') {
            // the requests not yet taken by the PC stay readable with the new key
            $table=tdbs_pc_table();
            foreach ((array)$wpdb->get_results("SELECT id,data FROM $table",ARRAY_A) as $row) {
                $client=tdbs_pc_open($row['data'],$new['secret']);
                if (is_array($client)) $wpdb->update($table,array('data'=>tdbs_pc_seal($client,$secret)),array('id'=>$row['id']));
            }
        }
        $new['secret']=$secret;
    }
    if ($new['on'] && $new['secret']==='') {$new['on']=false;tdbs_flash('Incolla prima la chiave generata nel programma.');update_option('tdbs_pc',$new,false);tdbs_back();}
    update_option('tdbs_pc',$new,false);
    tdbs_flash($new['on']?'Modalità PC attiva: il modulo usa i dati che manda il PC.':'Modalità PC salvata, spenta.','success');tdbs_back();
});
