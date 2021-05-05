**********************
README for APK Vitrine
**********************

a package tracker and analyzer for APK repositories

:Authors:
  **Max Rees**, maintainer
:Status:
  Beta
:Releases and source code:
  `Adélie Linux Gitlab <https://git.adelielinux.org/sroracle/apkvitrine>`_
:Copyright:
  © 2020 Max Rees. NCSA open source license.

Dependencies
------------

* Python 3.6+
* `Jinja2 <https://pypi.org/project/Jinja2/>`_ >= 2.10.3 (web
  application dependency)
* `Flup <https://pypi.org/project/flup/>`_ >= 1.0.3 (web application
  dependency)
* `APK Kit <https://pypi.org/project/apkkit/>`_ >= 6.0.6.1 (database
  builder dependency)
* libapk (database builder dependency)

Running the web application
---------------------------

Thanks to Flup, the web application can be run as either a traditional
CGI program, or using FastCGI. Here's an example Lighttpd configuration
for traditional CGI usage under the URL ``/pkg``::

    $HTTP["url"] =~ "^/pkg(/|$)" {
      alias.url += (
          "/pkg/static" => "/usr/share/webapps/apkvitrine/static",
          "/pkg" => "/usr/share/webapps/apkvitrine/apkvitrine.cgi",
      )

      cgi.assign = ("apkvitrine.cgi" => "")
    }

Here's FastCGI::

    var.apkvitrine_dir = "/usr/share/webapps/apkvitrine/"
    $HTTP["url"] =~ "^/pkg(/|$)" {
      alias.url += (
          "/pkg/static" => var.apkvitrine_dir + "static",
          "/pkg" => var.apkvitrine_dir + "apkvitrine.cgi",
      )

      fastcgi.server += ( "apkvitrine.cgi" => ( "apkvitrine" => (
          "docroot" => var.apkvitrine_dir,
          # Replace var.run_dir with wherever lighttpd can make a
          # socket.
          "socket" => var.run_dir + "apkvitrine.sock",
          "bin-path" => var.apkvitrine_dir + "apkvitrine.cgi",
          # Tune to your requirements.
          "max-procs" => 5,
      ) ) )
    }
