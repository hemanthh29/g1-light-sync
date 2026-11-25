package com.mitsukog1.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

public class MainActivity extends Activity {
    private static final String USB_BASE = "http://127.0.0.1:8765";
    private static final int BG = Color.rgb(7,16,14), CARD = Color.rgb(17,31,27), CARD2 = Color.rgb(23,40,35);
    private static final int TEXT = Color.rgb(242,247,244), MUTED = Color.rgb(155,177,166), GREEN = Color.rgb(70,214,170);
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView link, linkDetail, mode, brightnessValue;
    private LinearLayout profileRow;
    private SeekBar brightness;
    private int currentBrightness = 70;
    private final Runnable poll = new Runnable() { @Override public void run() { refresh(false); handler.postDelayed(this, 5000); } };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(BG); getWindow().setNavigationBarColor(BG);
        getWindow().getDecorView().setSystemUiVisibility(0);
        handleShortcut(getIntent());
        buildUi(); refresh(true);
    }

    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); handleShortcut(intent); }

    private void handleShortcut(Intent intent) {
        if (intent == null) return;
        String action = intent.getStringExtra("mitsuko_action");
        if (action == null) return;
        JSONObject fields = null;
        if ("music".equals(action)) fields = json("mode", "combined");
        send(action, fields);
    }

    @Override protected void onResume() { super.onResume(); handler.post(poll); }
    @Override protected void onPause() { handler.removeCallbacks(poll); super.onPause(); }
    @Override protected void onDestroy() { io.shutdownNow(); super.onDestroy(); }

    private int dp(float x) { return Math.round(x * getResources().getDisplayMetrics().density); }
    private GradientDrawable shape(int color, int radius) {
        GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius)); return d;
    }
    private TextView label(String value, int size, int color, boolean bold) {
        TextView t = new TextView(this); t.setText(value); t.setTextSize(size); t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD); return t;
    }
    private LinearLayout column() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); return l; }
    private LinearLayout row() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.HORIZONTAL); l.setGravity(Gravity.CENTER_VERTICAL); return l; }
    private LinearLayout card(int color) { LinearLayout l = column(); l.setPadding(dp(18),dp(16),dp(18),dp(16)); l.setBackground(shape(color,22)); return l; }
    private LinearLayout.LayoutParams lp(int width, int height) { return new LinearLayout.LayoutParams(width < 0 ? width : dp(width), height < 0 ? height : dp(height)); }
    private void gap(LinearLayout parent, int h) { View v = new View(this); parent.addView(v, lp(1,h)); }
    private void buildUi() {
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setBackgroundColor(BG);
        LinearLayout root = column(); root.setPadding(dp(20), dp(14), dp(20), dp(28)); scroll.addView(root);

        LinearLayout top = row();
        TextView mark = label("G1",15,BG,true); mark.setGravity(Gravity.CENTER); mark.setBackground(shape(GREEN,12)); top.addView(mark,lp(44,44));
        LinearLayout brand = column(); brand.setPadding(dp(12),0,0,0); brand.addView(label("MITSUKO",12,TEXT,true)); brand.addView(label("LIGHT SYNC",10,MUTED,true));
        top.addView(brand,new LinearLayout.LayoutParams(0,dp(44),1));
        TextView refresh = label("↻",26,TEXT,false); refresh.setGravity(Gravity.CENTER); refresh.setContentDescription("Refresh connection"); refresh.setOnClickListener(v->refresh(true)); top.addView(refresh,lp(48,48));
        root.addView(top); gap(root,22);

        root.addView(label("LIGHT,\nFROM YOUR PHONE.",30,TEXT,true)); gap(root,7);
        root.addView(label("Control your G1 over USB or the same Wi-Fi network.",14,MUTED,false)); gap(root,18);

        LinearLayout connection = card(CARD); LinearLayout cRow = row();
        TextView dot = label("●",16,GREEN,true); cRow.addView(dot,lp(26,24));
        LinearLayout cText = column(); link = label("Connecting to laptop…",14,TEXT,true); cText.addView(link); linkDetail = label("USB or Wi-Fi · laptop app must be open",11,MUTED,false); cText.addView(linkDetail);
        cRow.addView(cText,new LinearLayout.LayoutParams(0,-2,1));
        Button check = button("Check", false); cRow.addView(check,lp(76,42)); check.setOnClickListener(v->refresh(true));
        connection.addView(cRow);
        Button wifi = button("Wi-Fi setup · pair another laptop", false); LinearLayout.LayoutParams wifiLp=lp(-1,44); wifiLp.topMargin=dp(12); connection.addView(wifi,wifiLp); wifi.setOnClickListener(v->showWifiSetup());
        root.addView(connection); gap(root,18);

        LinearLayout hero = card(CARD2); hero.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,new int[]{Color.rgb(26,68,53),Color.rgb(18,40,34)}));
        hero.addView(label("NOW PLAYING",10,GREEN,true)); gap(hero,8);
        mode = label("Ready to sync",22,TEXT,true); hero.addView(mode); gap(hero,5);
        hero.addView(label("Start a mode below. Your laptop handles the LEDs.",12,MUTED,false));
        LinearLayout heroButtons = row(); heroButtons.setPadding(0,dp(16),0,0);
        Button stop = button("Stop", true); heroButtons.addView(stop,lp(98,44)); stop.setOnClickListener(v->send("stop",null));
        Button off = button("Lights off", false); LinearLayout.LayoutParams offLp=lp(112,44); offLp.leftMargin=dp(9); heroButtons.addView(off,offLp); off.setOnClickListener(v->send("off",null));
        hero.addView(heroButtons); root.addView(hero); gap(root,22);

        section(root,"QUICK MODES","Pick the mood for the room.");
        LinearLayout modes = row();
        modes.addView(modeTile("◉","Screen sync","Match the display", "screen"),new LinearLayout.LayoutParams(0,-2,1));
        View spacer = new View(this); modes.addView(spacer,lp(10,1));
        modes.addView(modeTile("♫","Music reactive","Move with the sound", "music"),new LinearLayout.LayoutParams(0,-2,1));
        root.addView(modes); gap(root,22);

        section(root,"BRIGHTNESS","Set the strip intensity.");
        LinearLayout bright = card(CARD); LinearLayout bRow=row(); bRow.addView(label("Brightness",14,TEXT,true),new LinearLayout.LayoutParams(0,-2,1));
        brightnessValue=label(currentBrightness+"%",13,GREEN,true); bRow.addView(brightnessValue); bright.addView(bRow); gap(bright,12);
        brightness=new SeekBar(this); brightness.setMax(100); brightness.setProgress(currentBrightness); bright.addView(brightness,lp(-1,34));
        brightness.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener(){
            public void onProgressChanged(SeekBar s,int p,boolean user){if(user){currentBrightness=p;brightnessValue.setText(p+"%");}}
            public void onStartTrackingTouch(SeekBar s){}
            public void onStopTrackingTouch(SeekBar s){send("brightness",json("value",Math.max(5,Math.round(currentBrightness*2.55f))));}
        });
        root.addView(bright); gap(root,22);

        section(root,"COLOR","A quick color, whenever you want it.");
        LinearLayout palette=row(); palette.setGravity(Gravity.CENTER_VERTICAL);
        addColor(palette,"#46D6AA",new int[]{70,214,170}); addColor(palette,"#7EA7FF",new int[]{126,167,255});
        addColor(palette,"#C49BFF",new int[]{196,155,255}); addColor(palette,"#FF8A70",new int[]{255,138,112});
        addColor(palette,"#FFD36D",new int[]{255,211,109}); addColor(palette,"#F4F4F0",new int[]{244,244,240});
        root.addView(palette); gap(root,22);

        section(root,"LIGHTING EFFECTS","Tap once to apply.");
        effectGroup(root,"Dynamic · built in",2); gap(root,8);
        effectGroup(root,"Pickup · sound reactive",3); gap(root,8);
        root.addView(label("APP EFFECTS",11,GREEN,true)); gap(root,8);
        String[] appEffects={"Color Flow","Aurora","Breathe","Comet","Ember"};
        for(int start=0;start<appEffects.length;start+=3){
            LinearLayout fx=row();
            for(int i=start;i<Math.min(start+3,appEffects.length);i++){
                final String name=appEffects[i].toLowerCase().replace(' ','_');
                Button b=button(appEffects[i],false); LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(46),1);
                if(i>start)p.leftMargin=dp(7); fx.addView(b,p); b.setOnClickListener(v->send("animate",json("name",name)));
            }
            root.addView(fx); gap(root,7);
        }
        gap(root,14);

        section(root,"YOUR PROFILES","Saved on the laptop.");
        profileRow=row(); profileRow.setGravity(Gravity.CENTER_VERTICAL); root.addView(profileRow); gap(root,15);
        TextView foot=label("For Wi-Fi, connect both devices to the same network. Keep Mitsuko G1 open on the laptop.",11,MUTED,false); foot.setGravity(Gravity.CENTER); root.addView(foot);
        setContentView(scroll);
    }

    private void section(LinearLayout root,String title,String sub){root.addView(label(title,11,GREEN,true)); gap(root,4); root.addView(label(sub,13,MUTED,false)); gap(root,11);}
    private void effectGroup(LinearLayout root,String title,int effectId){
        root.addView(label(title,12,TEXT,true)); gap(root,7);
        for(int start=1;start<=7;start+=4){
            LinearLayout fx=row();
            for(int n=start;n<Math.min(start+4,8);n++){
                final int preset=n-1; Button b=button(title.startsWith("Dynamic")?"Dynamic "+n:"Pickup "+n,false);
                LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(44),1); if(n>start)p.leftMargin=dp(6);
                fx.addView(b,p); b.setOnClickListener(v->send("effect",jsonPair("id",effectId,"param",preset)));
            }
            root.addView(fx); gap(root,6);
        }
    }
    private Button button(String text,boolean filled){Button b=new Button(this);b.setText(text);b.setAllCaps(false);b.setTextSize(13);b.setTypeface(Typeface.DEFAULT,Typeface.BOLD);b.setTextColor(filled?BG:TEXT);b.setBackground(shape(filled?GREEN:Color.rgb(35,54,47),14));b.setMinHeight(dp(42));b.setPadding(dp(12),0,dp(12),0);return b;}
    private View modeTile(String icon,String title,String sub,String action){LinearLayout c=card(CARD); TextView i=label(icon,23,GREEN,true); c.addView(i); gap(c,9); c.addView(label(title,16,TEXT,true)); gap(c,4); c.addView(label(sub,11,MUTED,false)); c.setMinimumHeight(dp(145)); c.setOnClickListener(v->{if(action.equals("music"))send("music",json("mode","combined"));else send(action,null);}); return c;}
    private void addColor(LinearLayout row,String hex,int[] rgb){TextView c=label("",1,TEXT,false); c.setGravity(Gravity.CENTER); GradientDrawable bg=shape(Color.rgb(rgb[0],rgb[1],rgb[2]),24);bg.setStroke(dp(2),Color.rgb(38,58,50));c.setBackground(bg);c.setContentDescription("Set color "+hex);LinearLayout.LayoutParams p=lp(39,39);p.rightMargin=dp(10);row.addView(c,p);c.setOnClickListener(v->send("color",json("rgb",new JSONArray().put(rgb[0]).put(rgb[1]).put(rgb[2]))));}
    private JSONObject json(String key,Object value){JSONObject o=new JSONObject();try{o.put(key,value);}catch(Exception ignored){}return o;}
    private JSONObject jsonPair(String k1,Object v1,String k2,Object v2){JSONObject o=json(k1,v1);try{o.put(k2,v2);}catch(Exception ignored){}return o;}
    private volatile String lastTransport="USB";
    private volatile long wifiRetryAfter=0;
    private volatile String discoveredWifiBase="";
    private String wifiBase(){return getPreferences(MODE_PRIVATE).getString("wifiBase","");}
    private String wifiToken(){return getPreferences(MODE_PRIVATE).getString("wifiToken","");}
    private void refresh(boolean notify){io.execute(()->{try{JSONObject st=get("/api/status");String via=lastTransport;JSONArray names=get("/api/profiles").optJSONArray("profiles");runOnUiThread(()->{
        boolean device=st.optBoolean("deviceConnected"), cap=st.optJSONObject("capture")!=null&&st.optJSONObject("capture").optBoolean("running");
        link.setText(device?"Laptop linked · G1 connected":"Laptop linked · G1 not detected");linkDetail.setText(via.equals("Wi-Fi")?"Wi-Fi · same network":"USB debugging · ADB tunnel");String m=st.optString("mode","idle");
        if(m.equals("screen"))mode.setText("Screen sync is on");else if(m.equals("music"))mode.setText("Music reactive is on");else mode.setText("Ready to sync");
        if(names!=null){profileRow.removeAllViews();for(int i=0;i<names.length();i++){String n=names.optString(i);Button b=button(n,false);LinearLayout.LayoutParams p=lp(-2,42);p.rightMargin=dp(8);profileRow.addView(b,p);b.setOnClickListener(v->send("screen",json("profile",n)));}}
        if(notify)Toast.makeText(this,device?"Laptop link ready · G1 connected" : (cap?"Laptop link ready; G1 not detected":"Laptop link ready"),Toast.LENGTH_SHORT).show();
    });}catch(Exception e){runOnUiThread(()->{link.setText("Laptop not found · check connection");linkDetail.setText("Same Wi-Fi or USB debugging");mode.setText("Waiting for laptop");if(notify)Toast.makeText(this,"Open Mitsuko G1 on the laptop and connect via Wi-Fi or USB",Toast.LENGTH_LONG).show();});}});}
    private JSONObject get(String path)throws Exception{return requestPreferred(path,"GET",null);}
    private JSONObject postAction(JSONObject data)throws Exception{return requestPreferred("/api/action","POST",data);}
    private JSONObject requestPreferred(String path,String method,JSONObject body)throws Exception{
        Exception wifiError=null;String token=wifiToken();
        if(!token.isEmpty()&&System.currentTimeMillis()>=wifiRetryAfter){
            String remembered=wifiBase();
            if(!remembered.isEmpty())try{JSONObject result=request(remembered+path,token,method,body,900,1800);lastTransport="Wi-Fi";wifiRetryAfter=0;return result;}
            catch(Exception e){wifiError=e;}
            String cached=discoveredWifiBase;
            if(!cached.isEmpty()&&!cached.equals(remembered))try{
                JSONObject result=request(cached+path,token,method,body,900,1800);lastTransport="Wi-Fi";wifiRetryAfter=0;
                getPreferences(MODE_PRIVATE).edit().putString("wifiBase",cached).apply();return result;
            }catch(Exception e){wifiError=e;}
            // Do not block a lighting tap on UDP discovery. Keep USB as the fast fallback,
            // then refresh the Wi-Fi address in the background for the next request.
            discoverWifiBasesAsync();
            wifiRetryAfter=System.currentTimeMillis()+3500;
        }
        try{JSONObject result=request(USB_BASE+path,null,method,body);lastTransport="USB";return result;}
        catch(Exception e){if(wifiError!=null)throw new Exception("Wi-Fi: "+wifiError.getMessage()+"; USB: "+e.getMessage());throw e;}
    }
    private void discoverWifiBasesAsync(){
        io.execute(()->{
            for(String base:discoverWifiBases()){
                if(!base.equals(wifiBase())){
                    discoveredWifiBase=base;
                    // Verify the candidate before persisting it; use the existing pair token.
                    try{request(base+"/api/status",wifiToken(),"GET",null);
                        getPreferences(MODE_PRIVATE).edit().putString("wifiBase",base).apply();
                    }catch(Exception ignored){}
                    break;
                }
            }
        });
    }
    private List<String> discoverWifiBases(){
        ArrayList<String> found=new ArrayList<>();
        try(DatagramSocket socket=new DatagramSocket()){
            socket.setBroadcast(true);socket.setSoTimeout(300);
            byte[] request="MITSUKO_G1_DISCOVER_V1".getBytes(StandardCharsets.UTF_8);
            socket.send(new DatagramPacket(request,request.length,InetAddress.getByName("255.255.255.255"),8766));
            long deadline=System.currentTimeMillis()+1200;
            while(System.currentTimeMillis()<deadline){
                try{byte[] buffer=new byte[512];DatagramPacket reply=new DatagramPacket(buffer,buffer.length);socket.receive(reply);
                    JSONObject data=new JSONObject(new String(reply.getData(),reply.getOffset(),reply.getLength(),StandardCharsets.UTF_8));
                    if(data.optString("service").equals("mitsuko-g1")&&data.optInt("port")==8765){
                        String base="http://"+reply.getAddress().getHostAddress()+":"+data.optInt("port");if(!found.contains(base))found.add(base);
                    }
                }catch(java.net.SocketTimeoutException ignored){}
            }
        }catch(Exception ignored){}
        return found;
    }
    private JSONObject request(String url,String token,String method,JSONObject body)throws Exception{
        return request(url,token,method,body,1200,3000);
    }
    private JSONObject request(String url,String token,String method,JSONObject body,int connectTimeout,int readTimeout)throws Exception{
        HttpURLConnection c=(HttpURLConnection)new URL(url).openConnection();c.setConnectTimeout(connectTimeout);c.setReadTimeout(readTimeout);c.setRequestMethod(method);c.setUseCaches(false);c.setRequestProperty("Connection","keep-alive");
        if(token!=null&&!token.isEmpty())c.setRequestProperty("Authorization","Bearer "+token);
        if(body!=null){c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);try(OutputStream out=c.getOutputStream()){out.write(bytes);}}
        int code=c.getResponseCode();InputStream in=code<400?c.getInputStream():c.getErrorStream();String response=read(in);c.disconnect();JSONObject result=new JSONObject(response);
        if(code>=400||!result.optBoolean("ok",true))throw new Exception(result.optString("error","HTTP "+code));return result;
    }
    private void send(String action,JSONObject fields){JSONObject data=json("action",action);if(fields!=null){java.util.Iterator<String> it=fields.keys();while(it.hasNext()){String k=it.next();try{data.put(k,fields.get(k));}catch(Exception ignored){}}}
        io.execute(()->{try{JSONObject result=postAction(data);runOnUiThread(()->Toast.makeText(this,result.optString("message","Done"),Toast.LENGTH_SHORT).show());}catch(Exception e){runOnUiThread(()->Toast.makeText(this,"Could not reach laptop: "+e.getMessage(),Toast.LENGTH_LONG).show());}});
    }
    private void showWifiSetup(){
        LinearLayout form=column();form.setPadding(dp(22),dp(8),dp(22),0);
        EditText host=new EditText(this);host.setSingleLine(true);host.setHint("Laptop Wi-Fi address, e.g. 192.168.1.5");host.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);form.addView(host,lp(-1,52));
        EditText pin=new EditText(this);pin.setSingleLine(true);pin.setHint("8-digit pairing code from laptop");pin.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_VARIATION_PASSWORD);form.addView(pin,lp(-1,52));
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Connect over Wi-Fi")
            .setMessage("Put your phone and laptop on the same Wi-Fi. In Mitsuko G1 on the laptop, tap Phone Wi-Fi setup to see its address and pairing code.")
            .setView(form).setNegativeButton("Cancel",null).setPositiveButton("Pair",null).create();
        dialog.setOnShowListener(v->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view->{
            String address=host.getText().toString().trim();String code=pin.getText().toString().trim();
            if(address.startsWith("http://"))address=address.substring(7);if(address.endsWith("/"))address=address.substring(0,address.length()-1);if(!address.contains(":"))address+=":8765";
            if(address.isEmpty()||code.length()!=8){Toast.makeText(this,"Enter the laptop address and 8-digit code",Toast.LENGTH_SHORT).show();return;}
            final String base="http://"+address;final String pairingCode=code;dialog.dismiss();
            io.execute(()->{try{JSONObject result=request(base+"/api/pair",null,"POST",json("pin",pairingCode));
                getPreferences(MODE_PRIVATE).edit().putString("wifiBase",base).putString("wifiToken",result.getString("token")).apply();
                runOnUiThread(()->{Toast.makeText(this,"Paired over Wi-Fi",Toast.LENGTH_SHORT).show();refresh(true);});
            }catch(Exception e){runOnUiThread(()->Toast.makeText(this,"Wi-Fi pairing failed: "+e.getMessage(),Toast.LENGTH_LONG).show());}});
        }));dialog.show();
    }
    private String read(InputStream in)throws Exception{StringBuilder s=new StringBuilder();try(BufferedReader r=new BufferedReader(new InputStreamReader(in,StandardCharsets.UTF_8))){String line;while((line=r.readLine())!=null)s.append(line);}return s.toString();}
}
