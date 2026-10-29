<?php
/**
 * Plugin Name: TD Booking Suite — Prenotazioni
 * Description: Mostra nel sito il modulo di prenotazione di TD Booking Suite (Cloud o Private). Si collega al portale con un codice e si configura da solo. Nessun dato personale nel database WordPress.
 * Version: 2.10.0
 * Requires at least: 6.2
 * Requires PHP: 8.0
 * Author: Tastiere Digitali
 * License: GPL-2.0-or-later
 */
defined('ABSPATH') || exit;

const TDBS_VERSION='2.10.0';

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
        echo '</tbody></table><p class="description">Tema facoltativo: <code>theme="dark"</code> o <code>theme="light"</code>. Il modulo legge sedi, servizi e orari dal portale in tempo reale: se l’attività cambia, il sito si aggiorna da solo. Aggiorna qui solo per rivedere l’elenco degli shortcode.</p><p>';
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
    echo '</form></details><p>Il plugin non conserva nomi, recapiti, note o credenziali nel database WordPress: il modulo arriva dal portale, dentro un riquadro della pagina.</p></div>';
}

function tdbs_shortcode($atts,$content='',$tag='td_booking_suite') {
    $s=tdbs_settings();
    if (!tdbs_connected()) return '<p>Prenotazioni temporaneamente non disponibili. Contatta l’attività.</p>';
    $atts=shortcode_atts(array('theme'=>'auto','service'=>'','location'=>''),$atts,$tag);
    $theme=in_array($atts['theme'],array('auto','light','dark'),true)?$atts['theme']:'auto';
    $src=$s['origin'].'/book/'.rawurlencode($s['tenant']).'/?theme='.$theme;
    $service=trim((string)$atts['service']);
    if (preg_match('/^[a-f0-9-]{36}$/i',$service)) $src.='&service='.rawurlencode($service);
    $place=trim((string)$atts['location']);
    if (preg_match('/^[a-f0-9-]{36}$/i',$place)) $src.='&location='.rawurlencode($place);
    wp_enqueue_style('tdbs-widget',plugins_url('widget.css',__FILE__),array(),TDBS_VERSION);
    wp_enqueue_script('tdbs-widget',plugins_url('widget.js',__FILE__),array(),TDBS_VERSION,true);
    return '<div class="tdbs-embed"><iframe class="tdbs-frame" src="'.esc_url($src).'" data-origin="'.esc_attr($s['origin']).'" title="Prenota" width="100%" height="1300" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-top-navigation-by-user-activation"></iframe><p><a target="_blank" rel="noopener noreferrer" href="'.esc_url($src).'">Apri le prenotazioni in una nuova pagina</a></p></div>';
}
add_action('init',function(){add_shortcode('td_booking_suite','tdbs_shortcode');});
